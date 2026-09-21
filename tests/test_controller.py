
from datetime import datetime, timedelta, timezone

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
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "PAS-4"),
        )
    decision = MissionController(store).evaluate("PAS-4")
    assert decision.action == ControllerAction.REASSIGN


def test_review_state_never_self_completes(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission("PAS-5", "repo", "goal", status=MissionStatus.IN_REVIEW)
    )
    decision = MissionController(store).evaluate("PAS-5")
    assert decision.action == ControllerAction.REVIEW
