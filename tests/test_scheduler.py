from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from mission_control.adapters.base import AgentExecution
from mission_control.git_executor import RepositoryState, WorktreeAssignment
from mission_control.lease import LeaseManager
from mission_control.models import Mission, MissionStatus
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


def test_expired_clean_writer_is_reassigned(tmp_path):
    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)
    LeaseManager(store).claim("M-0", "old-agent", ttl_seconds=600)
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
                "old-exec", "M-0", "old-agent", "codex", "LOST", 1,
                "/worktrees/old", "[]", "/tmp/out", "/tmp/err", "/tmp/result",
                now, now,
            ),
        )
    snapshot = scheduler.tick()
    assert len(snapshot.assigned) == 1
    assert snapshot.assigned[0].takeover is True
    assert snapshot.assigned[0].agent_id != "old-agent"


def test_daily_report_summarizes_statuses(tmp_path):
    store = setup_store(tmp_path, 2)
    store.set_status("M-1", MissionStatus.IN_REVIEW)
    scheduler = build_scheduler(store, tmp_path)
    report = scheduler.daily_report()
    assert report["status_counts"]["READY"] == 1
    assert report["status_counts"]["IN_REVIEW"] == 1
    assert "M-1: IN_REVIEW" in report["reminders"]

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
