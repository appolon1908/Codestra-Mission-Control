
from datetime import UTC, datetime, timedelta

from mission_control.controller import ControllerAction, MissionController
from mission_control.lease import LeaseManager
from mission_control.models import Mission, MissionStatus
from mission_control.store import MissionStore


def test_ready_mission_without_lease_is_reassignable(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-3", "repo", "goal"))
    decision = MissionController(store).evaluate("PAS-3")
    assert decision.action == ControllerAction.REASSIGN


def test_expired_writer_is_reassigned(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-4", "repo", "goal"))
    LeaseManager(store).claim("PAS-4", "codex-01")
    with store.connection() as conn:
        conn.execute(
            "UPDATE leases SET expires_at=? WHERE mission_id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "PAS-4"),
        )
    decision = MissionController(store).evaluate("PAS-4")
    assert decision.action == ControllerAction.REASSIGN


def test_review_state_without_proven_delivery_reassigns_implementation(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission("PAS-5", "repo", "goal", status=MissionStatus.IN_REVIEW)
    )
    decision = MissionController(store).evaluate("PAS-5")
    assert decision.action == ControllerAction.REASSIGN
    assert "implementation agent" in decision.reason


def test_review_state_with_proven_delivery_moves_agent_to_next_implementation(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission("PAS-6", "repo", "goal", status=MissionStatus.IN_REVIEW)
    )
    store.start_implementation_execution(
        execution_id="impl-pas6",
        mission_id="PAS-6",
        agent_id="codex-01",
        workstation="codestra-desktop",
        provider="codex",
        branch="mission/pas6",
        worktree="/tmp/pas6",
        api_required=False,
    )
    store.record_implementation_proof(
        "impl-pas6",
        implementation_files=["src/change.py"],
        api_endpoints=[],
        tests={"passed": True},
        local_commit_sha="abc",
        pushed_branch_sha="abc",
        pr_number=6,
        pr_url="https://github.com/example/repo/pull/6",
        pr_head_sha="abc",
    )
    decision = MissionController(store).evaluate("PAS-6")
    assert decision.action == ControllerAction.NEXT_IMPLEMENTATION
    assert "external review/CI" in decision.reason
