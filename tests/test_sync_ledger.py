from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mission_control.control_sync import (
    CheckpointEnvelope,
    CheckpointFanout,
    Surface,
    SurfaceObservation,
    SyncState,
    reconcile,
)
from mission_control.lease import LeaseManager
from mission_control.models import Mission
from mission_control.store import MissionStore

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def make_store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-250", "repo", "sync"))
    return store


def test_ledger_keeps_last_success_and_head_across_outage(tmp_path):
    store = make_store(tmp_path)
    store.record_surface_observation(
        "PAS-250",
        SurfaceObservation(Surface.GITHUB, True, "OPEN", "abc123", observed_at=T0),
        agent_id="writer-1",
    )
    later = T0 + timedelta(minutes=5)
    stored = store.record_surface_observation(
        "PAS-250",
        SurfaceObservation(Surface.GITHUB, False, "ERROR", error="URLError", observed_at=later),
        agent_id="writer-1",
    )
    assert stored.available is False
    assert stored.head_sha == "abc123"
    assert stored.last_success_at == T0
    assert stored.last_error_at == later
    assert stored.error == "URLError"

    recovered = store.record_surface_observation(
        "PAS-250",
        SurfaceObservation(Surface.GITHUB, True, "OPEN", "abc123", observed_at=later + timedelta(1)),
    )
    assert recovered.error is None
    assert recovered.last_error_at == later
    assert recovered.last_success_at == later + timedelta(1)


def test_ledger_ignores_out_of_order_observation(tmp_path):
    store = make_store(tmp_path)
    store.record_surface_observation(
        "PAS-250",
        SurfaceObservation(Surface.LOCAL, True, "OK", "new", observed_at=T0),
    )
    stored = store.record_surface_observation(
        "PAS-250",
        SurfaceObservation(Surface.LOCAL, True, "OK", "old", observed_at=T0 - timedelta(1)),
    )
    assert stored.head_sha == "new"
    assert store.surface_observations("PAS-250")[0].head_sha == "new"


def test_fanout_with_ledger_and_lease_owner(tmp_path):
    store = make_store(tmp_path)
    leases = LeaseManager(store)
    leases.claim("PAS-250", "writer-1")

    class Up:
        def __init__(self, surface):
            self.surface = surface

        def publish_checkpoint(self, checkpoint):
            return SurfaceObservation(self.surface, True, "OK", checkpoint.head_sha)

    class Down:
        surface = Surface.NOTION

        def publish_checkpoint(self, checkpoint):
            raise ConnectionError("notion down")

    fanout = CheckpointFanout(
        [Up(Surface.LOCAL), Up(Surface.LINEAR), Up(Surface.GITHUB), Down()],
        owner_check=leases.is_owner,
        ledger=store,
        clock=lambda: T0,
    )
    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    decision = fanout.publish(cp, agent_id="writer-1")
    assert decision.missing == (Surface.NOTION,)
    assert decision.reconciled == (Surface.LOCAL, Surface.LINEAR, Surface.GITHUB)

    refused = fanout.publish(cp, agent_id="other")
    assert refused.state == SyncState.BLOCKED
    assert refused.conflicts[0].kind.value == "OWNERSHIP"

    readback = reconcile(cp, store.surface_observations("PAS-250"), now=T0)
    assert readback.reconciled == (Surface.LOCAL, Surface.LINEAR, Surface.GITHUB)
    notion = {s.surface: s for s in readback.sources}[Surface.NOTION]
    assert notion.error == "ConnectionError"
    assert notion.last_error_at == T0
    events = [row["event_type"] for row in store.events("PAS-250")]
    assert events.count("SURFACE_OBSERVED") == 4


def _cli(db, *args, check=True):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "mission_control.cli", "--db", str(db), *args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def test_cli_sync_observe_requires_lease_and_readback_reports_sources(tmp_path):
    store = make_store(tmp_path)
    db = store.path
    refused = _cli(
        db, "sync-observe", "--mission", "PAS-250", "--agent", "writer-1",
        "--surface", "local", "--status", "OK", "--head-sha", "abc123",
        check=False,
    )
    assert refused.returncode != 0
    assert "ownership refused" in refused.stderr

    LeaseManager(store).claim("PAS-250", "writer-1")
    for surface in ("local", "linear", "github"):
        out = _cli(
            db, "sync-observe", "--mission", "PAS-250", "--agent", "writer-1",
            "--surface", surface, "--status", "OK", "--head-sha", "abc123",
        )
        assert json.loads(out.stdout)["source"]["freshness"] == "FRESH"
    _cli(
        db, "sync-observe", "--mission", "PAS-250", "--agent", "writer-1",
        "--surface", "notion", "--status", "ERROR", "--unavailable", "--error", "HTTP503",
    )
    out = _cli(db, "sync-readback", "--mission", "PAS-250", "--head-sha", "abc123")
    payload = json.loads(out.stdout)
    decision = payload["decision"]
    assert decision["state"] == "BLOCKED"
    assert decision["missing"] == ["notion"]
    assert decision["reconciled"] == ["local", "linear", "github"]
    notion = [s for s in decision["sources"] if s["surface"] == "notion"][0]
    assert notion["freshness"] == "UNREACHABLE"
    assert notion["error"] == "HTTP503"
    assert notion["last_error_at"] is not None
    assert notion["last_success_at"] is None

    expired = _cli(
        db, "sync-readback", "--mission", "PAS-250", "--head-sha", "abc123",
        "--required", "local,linear,github", "--max-age-seconds", "0",
    )
    assert json.loads(expired.stdout)["decision"]["expired"] == ["local", "linear", "github"]
