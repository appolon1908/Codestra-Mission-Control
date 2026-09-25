from mission_control.checkpoint_dispatcher import (
    CheckpointAction,
    CheckpointDispatcher,
)
from mission_control.models import Mission, MissionStatus
from mission_control.store import MissionStore


def setup_store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    channel = tmp_path / "channel"
    store.upsert_repository(
        "repo",
        full_name="org/repo",
        local_path=str(tmp_path / "repo"),
        origin_url="https://example.invalid/org/repo.git",
        default_branch="main",
        visibility="private",
        local_present=True,
        mission_channel_path=str(channel),
        workspace_path=None,
    )
    store.upsert_mission(
        Mission("PAS-X", "repo", "implement bounded task", status=MissionStatus.WORKING)
    )
    return store, channel


def checkpoint(store, **overrides):
    data = {
        "mission_id": "PAS-X",
        "agent_id": "codex-01",
        "state": "IMPLEMENTED",
        "head_sha": "a" * 40,
        "dirty_count": 0,
        "tests": {"pytest": {"passed": True}},
        "blockers": [],
        "next_task_requested": True,
    }
    data.update(overrides)
    mission_id = data.pop("mission_id")
    agent_id = data.pop("agent_id")
    state = data.pop("state")
    store.record_checkpoint(mission_id, agent_id, state, **data)


def test_clean_tested_handoff_moves_to_review_and_writes_next_task(tmp_path):
    store, channel = setup_store(tmp_path)
    checkpoint(store)

    decision = CheckpointDispatcher(store).process_latest("PAS-X")

    assert decision.action == CheckpointAction.REVIEW
    assert store.get_mission("PAS-X")["status"] == MissionStatus.IN_REVIEW.value
    text = (channel / "NEXT_TASK.md").read_text()
    assert "Independently review and verify exact HEAD" in text
    assert "a" * 40 in text


def test_blocker_fails_closed_and_surfaces_first_blocker(tmp_path):
    store, channel = setup_store(tmp_path)
    checkpoint(store, blockers=["GitHub authentication unavailable"])

    decision = CheckpointDispatcher(store).process_latest("PAS-X")

    assert decision.action == CheckpointAction.BLOCKED
    assert store.get_mission("PAS-X")["status"] == MissionStatus.BLOCKED.value
    assert "GitHub authentication unavailable" in (channel / "NEXT_TASK.md").read_text()


def test_dirty_or_failed_checkpoint_requires_rework(tmp_path):
    store, _channel = setup_store(tmp_path)
    checkpoint(store, dirty_count=2)
    assert CheckpointDispatcher(store).process_latest("PAS-X").action == CheckpointAction.REWORK

    checkpoint(
        store,
        dirty_count=0,
        tests={"pytest": {"status": "failed"}},
    )
    decision = CheckpointDispatcher(store).process_latest("PAS-X")
    assert decision.action == CheckpointAction.REWORK
    assert store.get_mission("PAS-X")["status"] == MissionStatus.WORKING.value


def test_handoff_without_tests_or_head_is_rejected(tmp_path):
    store, _channel = setup_store(tmp_path)
    checkpoint(store, tests={})
    decision = CheckpointDispatcher(store).evaluate("PAS-X")
    assert decision.action == CheckpointAction.REWORK
    assert "without test evidence" in decision.reason

    checkpoint(store, tests={"pytest": {"passed": True}}, head_sha=None)
    decision = CheckpointDispatcher(store).evaluate("PAS-X")
    assert decision.action == CheckpointAction.REWORK
    assert "without an exact HEAD SHA" in decision.reason


def test_checkpoint_without_handoff_request_continues_work(tmp_path):
    store, _channel = setup_store(tmp_path)
    checkpoint(store, next_task_requested=False)

    decision = CheckpointDispatcher(store).process_latest("PAS-X")

    assert decision.action == CheckpointAction.CONTINUE
    assert store.get_mission("PAS-X")["status"] == MissionStatus.WORKING.value
