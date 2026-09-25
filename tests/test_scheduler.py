from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from mission_control.adapters.base import AgentExecution
from mission_control.git_executor import RepositoryState, WorktreeAssignment
from mission_control.lease import LeaseManager
from mission_control.models import AgentRole, DispatchState, Mission, MissionStatus
from mission_control.scheduler import MissionScheduler, WorkerSlot
from mission_control.store import MissionStore


class FakeGit:
    def create(self, repo, *, mission_id, agent_id, base_ref, worktree_root, branch=None):
        return WorktreeAssignment(
            repository="repo",
            mission_id=mission_id,
            agent_id=agent_id,
            branch=branch or f"mission/{mission_id.lower()}-{agent_id}",
            worktree=f"/worktrees/{mission_id}-{agent_id}",
            base_sha="abc123",
            head_sha="abc123",
        )

    def takeover(self, repo, *, mission_id, new_agent_id, checkpoint_head, worktree_root):
        return self.create(
            repo,
            mission_id=mission_id,
            agent_id=new_agent_id,
            base_ref=checkpoint_head,
            worktree_root=worktree_root,
        )

    def inspect(self, repo):
        return RepositoryState(
            path=str(repo),
            branch="mission/x",
            head_sha="abc123",
            dirty_count=0,
            upstream=None,
            ahead=None,
            behind=None,
        )


@dataclass
class FakeAdapter:
    name: str

    def dispatch(self, assignment):
        return AgentExecution(
            execution_id=f"exec-{assignment.mission_id}-{assignment.agent_id}",
            mission_id=assignment.mission_id,
            agent_id=assignment.agent_id,
            provider=self.name,
            state="RUNNING",
            worktree=assignment.worktree,
        )


def setup_store(tmp_path, count=4):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_repository(
        "repo",
        full_name="owner/repo",
        local_path="/repos/repo",
        origin_url=None,
        default_branch="main",
        visibility="private",
        local_present=True,
        mission_channel_path="/repos/repo/.codestra-mission",
        workspace_path="/hub/repo.code-workspace",
    )
    for index in range(count):
        store.upsert_mission(
            Mission(
                f"M-{index}",
                "repo",
                f"goal {index}",
                status=MissionStatus.READY,
                acceptance=["tests pass"],
            )
        )
    return store


def build_scheduler(store, tmp_path):
    return MissionScheduler(
        store,
        workers=(
            WorkerSlot("codex-01", "codex"),
            WorkerSlot("claude-01", "claude"),
            WorkerSlot("codex-02", "codex"),
        ),
        adapters={
            "codex": FakeAdapter("codex"),
            "claude": FakeAdapter("claude"),
        },
        worktree_root=tmp_path / "worktrees",
        priorities={"M-2": 1, "M-0": 2, "M-1": 3, "M-3": 4},
        max_parallel_writers=3,
        git=FakeGit(),
    )


def test_scheduler_caps_parallel_writers_at_three(tmp_path):
    store = setup_store(tmp_path, 4)
    scheduler = build_scheduler(store, tmp_path)
    snapshot = scheduler.tick()
    assert len(snapshot.assigned) == 3
    assert snapshot.active_writers == 3
    assigned = [item.mission_id for item in snapshot.assigned]
    assert assigned == ["M-2", "M-0", "M-1"]
    assert store.get_mission("M-3")["status"] == MissionStatus.READY.value


def test_blocked_mission_is_not_dispatched(tmp_path):
    store = setup_store(tmp_path, 1)
    store.set_status("M-0", MissionStatus.BLOCKED)
    scheduler = build_scheduler(store, tmp_path)
    snapshot = scheduler.tick()
    assert snapshot.assigned == ()
    assert "M-0: BLOCKED" in snapshot.reminders


def test_expired_clean_writer_is_reassigned_to_different_agent(tmp_path):
    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)
    LeaseManager(store).claim("M-0", "codex-01", ttl_seconds=600)
    with store.connection() as conn:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE leases SET expires_at=? WHERE mission_id='M-0'",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO agent_executions (
              execution_id, mission_id, agent_id, provider, state, runner_pid,
              worktree, command_json, stdout_path, stderr_path, result_path,
              started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-exec", "M-0", "codex-01", "codex", "LOST", 1,
                "/worktrees/old", "[]", "/tmp/out", "/tmp/err", "/tmp/result",
                now, now,
            ),
        )
    snapshot = scheduler.tick()
    assert len(snapshot.assigned) == 1
    assert snapshot.assigned[0].takeover is True
    assert snapshot.assigned[0].agent_id == "claude-01"


def test_expired_writer_without_replacement_is_blocked(tmp_path):
    store = setup_store(tmp_path, 1)
    scheduler = MissionScheduler(
        store,
        workers=(WorkerSlot("codex-01", "codex"),),
        adapters={"codex": FakeAdapter("codex")},
        worktree_root=tmp_path / "worktrees",
        max_parallel_writers=1,
        git=FakeGit(),
    )
    LeaseManager(store).claim("M-0", "codex-01", ttl_seconds=600)
    with store.connection() as conn:
        conn.execute(
            "UPDATE leases SET expires_at=? WHERE mission_id='M-0'",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )

    snapshot = scheduler.tick()

    assert snapshot.assigned == ()
    assert snapshot.expired == ("M-0",)
    assert snapshot.blocked == (
        "M-0: expired writer codex-01 has no different replacement worker available",
    )


def test_daily_report_summarizes_statuses(tmp_path):
    store = setup_store(tmp_path, 2)
    store.set_status("M-1", MissionStatus.IN_REVIEW)
    scheduler = build_scheduler(store, tmp_path)
    report = scheduler.daily_report()
    assert report["status_counts"]["READY"] == 1
    assert report["status_counts"]["IN_REVIEW"] == 1
    assert "M-1: IN_REVIEW" in report["reminders"]


def test_scheduler_honors_persisted_reviewer_redispatch(tmp_path):
    store = setup_store(tmp_path, 1)
    store.update_mission_workspace(
        "M-0",
        branch="mission/m-0",
        worktree="/worktrees/M-0",
        base_sha="abc123",
        head_sha="abc123",
    )
    store.set_status("M-0", MissionStatus.IN_REVIEW)
    dispatch_id = store.request_dispatch(
        "M-0",
        role=AgentRole.REVIEWER,
        reason="exact-head review required",
        head_sha="abc123",
    )
    scheduler = MissionScheduler(
        store,
        workers=(WorkerSlot("claude-review", "claude", role=AgentRole.REVIEWER),),
        adapters={"claude": FakeAdapter("claude")},
        worktree_root=tmp_path / "worktrees",
        git=FakeGit(),
    )

    snapshot = scheduler.tick()

    assert len(snapshot.assigned) == 1
    assert snapshot.assigned[0].agent_id == "claude-review"
    lease = LeaseManager(store).current("M-0")
    assert lease["role"] == AgentRole.REVIEWER.value
    rows = store.list_dispatch_requests(states=(DispatchState.RUNNING,))
    assert len(rows) == 1
    assert rows[0]["id"] == dispatch_id
    launches = store.list_agent_launches()
    assert len(launches) == 1
    assert launches[0]["mission_id"] == "M-0"
    assert launches[0]["agent_id"] == "claude-review"
    assert launches[0]["role"] == AgentRole.REVIEWER.value


def test_scheduler_does_not_count_reviewer_as_active_writer(tmp_path):
    store = setup_store(tmp_path, 1)
    store.update_mission_workspace(
        "M-0",
        branch="mission/m-0",
        worktree="/worktrees/M-0",
        base_sha="abc123",
        head_sha="abc123",
    )
    store.set_status("M-0", MissionStatus.IN_REVIEW)
    store.request_dispatch(
        "M-0",
        role=AgentRole.REVIEWER,
        reason="review",
        head_sha="abc123",
    )
    scheduler = MissionScheduler(
        store,
        workers=(WorkerSlot("reviewer", "claude", role=AgentRole.REVIEWER),),
        adapters={"claude": FakeAdapter("claude")},
        worktree_root=tmp_path / "worktrees",
        git=FakeGit(),
    )
    snapshot = scheduler.tick()
    assert snapshot.active_writers == 0


def test_scheduler_records_launch_telemetry(tmp_path):
    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)
    snapshot = scheduler.tick()
    assert len(snapshot.assigned) == 1
    rows = store.list_agent_launches()
    assert len(rows) == 1
    assert rows[0]["mission_id"] == "M-0"
    assert rows[0]["agent_id"] == snapshot.assigned[0].agent_id
    assert rows[0]["state"] == "RUNNING"
def test_three_independent_missions_run_in_parallel_without_shared_lease(tmp_path):
    store = setup_store(tmp_path, 3)
    scheduler = build_scheduler(store, tmp_path)

    snapshot = scheduler.tick()

    assert snapshot.active_writers == 3
    assert len(snapshot.assigned) == 3
    mission_ids = {item.mission_id for item in snapshot.assigned}
    agent_ids = {item.agent_id for item in snapshot.assigned}
    assert mission_ids == {"M-0", "M-1", "M-2"}
    assert len(agent_ids) == 3
    for item in snapshot.assigned:
        lease = LeaseManager(store).current(item.mission_id)
        assert lease is not None
        assert lease["agent_id"] == item.agent_id


def test_blocked_mission_does_not_busy_loop_dispatch(tmp_path):
    store = setup_store(tmp_path, 1)
    store.set_status("M-0", MissionStatus.BLOCKED)
    scheduler = build_scheduler(store, tmp_path)

    first = scheduler.tick()
    second = scheduler.tick()

    assert first.assigned == ()
    assert second.assigned == ()
    assert first.active_writers == 0
    assert second.active_writers == 0
    assert first.reminders == ("M-0: BLOCKED",)
    assert second.reminders == ("M-0: BLOCKED",)


def test_daily_report_exposes_blocked_review_and_approval_required_work(tmp_path):
    store = setup_store(tmp_path, 4)
    store.set_status("M-0", MissionStatus.BLOCKED)
    store.set_status("M-1", MissionStatus.IN_REVIEW)
    store.set_status("M-2", MissionStatus.MERGE_READY)
    scheduler = build_scheduler(store, tmp_path)

    report = scheduler.daily_report()

    assert report["status_counts"]["BLOCKED"] == 1
    assert report["status_counts"]["IN_REVIEW"] == 1
    assert report["status_counts"]["MERGE_READY"] == 1
    assert report["blocked"] == ["M-0"]
    assert report["review"] == ["M-1"]
    assert report["approval_required"] == ["M-2"]
    assert "M-0: BLOCKED" in report["reminders"]
    assert "M-1: IN_REVIEW" in report["reminders"]
    assert "M-2: MERGE_READY" in report["reminders"]

