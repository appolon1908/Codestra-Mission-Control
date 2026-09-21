from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from mission_control.lease import LeaseManager
from mission_control.models import Mission, MissionStatus
from mission_control.store import MissionStore
from mission_control.temporal_runtime import MissionActivities, TemporalRuntimeConfig


def test_temporal_config_from_env(monkeypatch):
    monkeypatch.setenv("TEMPORAL_ADDRESS", "100.76.208.87:7233")
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "codestra-mission-control")
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", "mission-workers")
    monkeypatch.setenv("MISSION_CONTROL_DB", "state.db")
    monkeypatch.setenv("MISSION_CONTROL_POLL_SECONDS", "15")

    config = TemporalRuntimeConfig.from_env()
    assert config.address == "100.76.208.87:7233"
    assert config.namespace == "codestra-mission-control"
    assert config.task_queue == "mission-workers"
    assert config.database == "state.db"
    assert config.poll_seconds == 15


def test_activity_requests_dispatch_when_no_writer(tmp_path):
    db = tmp_path / "mission.db"
    store = MissionStore(db)
    store.initialize()
    store.upsert_mission(Mission("PAS-182", "repo", "temporal"))

    activities = MissionActivities(db)
    decision = asyncio.run(activities.evaluate_mission("PAS-182"))
    assert decision["action"] == "REASSIGN"

    result = asyncio.run(activities.dispatch_next_agent("PAS-182"))
    assert result["status"] == "DISPATCH_REQUESTED"

    event_types = [row["event_type"] for row in store.events("PAS-182")]
    assert "TEMPORAL_EVALUATED" in event_types
    assert "DISPATCH_REQUESTED" in event_types


def test_activity_detects_expired_writer_for_takeover(tmp_path):
    db = tmp_path / "mission.db"
    store = MissionStore(db)
    store.initialize()
    store.upsert_mission(Mission("PAS-183", "repo", "worktree"))
    LeaseManager(store).claim("PAS-183", "codex-01")

    with store.connection() as conn:
        conn.execute(
            "UPDATE leases SET expires_at=? WHERE mission_id=?",
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "PAS-183",
            ),
        )

    decision = asyncio.run(
        MissionActivities(db).evaluate_mission("PAS-183")
    )
    assert decision["action"] == "REASSIGN"
    assert "expired" in decision["reason"]


def test_review_state_requests_review(tmp_path):
    db = tmp_path / "mission.db"
    store = MissionStore(db)
    store.initialize()
    store.upsert_mission(
        Mission(
            "PAS-184",
            "repo",
            "adapter",
            status=MissionStatus.IN_REVIEW,
        )
    )

    activities = MissionActivities(db)
    decision = asyncio.run(activities.evaluate_mission("PAS-184"))
    assert decision["action"] == "REVIEW"
    result = asyncio.run(activities.request_review("PAS-184"))
    assert result["status"] == "REVIEW_REQUESTED"


def test_production_approval_is_not_auto_recorded(tmp_path):
    db = tmp_path / "mission.db"
    store = MissionStore(db)
    store.initialize()
    store.upsert_mission(Mission("PAS-185", "repo", "sync"))
    activities = MissionActivities(db)

    asyncio.run(activities.dispatch_next_agent("PAS-185"))

    with store.connection() as conn:
        count = conn.execute(
            "SELECT count(*) FROM approvals WHERE mission_id=?",
            ("PAS-185",),
        ).fetchone()[0]
    assert count == 0
