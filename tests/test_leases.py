
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mission_control.lease import LeaseConflict, LeaseManager, LeaseNotOwned
from mission_control.models import Mission
from mission_control.store import MissionStore


def make_store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-TEST", "repo", "goal"))
    return store


def test_writer_lease_is_exclusive(tmp_path):
    store = make_store(tmp_path)
    leases = LeaseManager(store)
    leases.claim("PAS-TEST", "codex-01", ttl_seconds=600)
    with pytest.raises(LeaseConflict):
        leases.claim("PAS-TEST", "claude-01", ttl_seconds=600)


def test_expired_lease_can_be_taken_over(tmp_path):
    store = make_store(tmp_path)
    leases = LeaseManager(store)
    leases.claim("PAS-TEST", "codex-01", ttl_seconds=600)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with store.connection() as conn:
        conn.execute(
            "UPDATE leases SET expires_at=? WHERE mission_id=?",
            (expired, "PAS-TEST"),
        )
    result = leases.claim("PAS-TEST", "claude-01", ttl_seconds=600)
    assert result.takeover is True
    assert leases.current("PAS-TEST")["agent_id"] == "claude-01"


def test_only_owner_can_heartbeat(tmp_path):
    store = make_store(tmp_path)
    leases = LeaseManager(store)
    leases.claim("PAS-TEST", "codex-01", ttl_seconds=600)
    with pytest.raises(LeaseNotOwned):
        leases.heartbeat("PAS-TEST", "claude-01")
