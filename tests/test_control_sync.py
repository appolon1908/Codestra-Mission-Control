from datetime import UTC, datetime, timedelta

import pytest

from mission_control.control_sync import (
    CheckpointEnvelope,
    CheckpointFanout,
    ConflictKind,
    SourceFreshness,
    Surface,
    SurfaceObservation,
    SyncState,
    reconcile,
)


def observations(head: str):
    return [
        SurfaceObservation(Surface.LOCAL, True, "OK", head),
        SurfaceObservation(Surface.LINEAR, True, "In Progress", head),
        SurfaceObservation(Surface.GITHUB, True, "CI_GREEN", head),
        SurfaceObservation(Surface.NOTION, True, "SYNCED", head),
    ]


def test_exact_sha_allows_completion():
    cp = CheckpointEnvelope("PAS-185", "CERTIFIED", "abc123", request_complete=True)
    decision = reconcile(cp, observations("abc123"))
    assert decision.state == SyncState.COMPLETE_ALLOWED
    assert decision.can_complete is True


def test_missing_surface_blocks_false_complete():
    cp = CheckpointEnvelope("PAS-185", "CERTIFIED", "abc123", request_complete=True)
    obs = observations("abc123")[:-1]
    decision = reconcile(cp, obs)
    assert decision.state == SyncState.BLOCKED
    assert Surface.NOTION in decision.missing
    assert decision.can_complete is False


def test_stale_github_sha_blocks():
    cp = CheckpointEnvelope("PAS-185", "CERTIFIED", "abc123", request_complete=True)
    obs = observations("abc123")
    obs[2] = SurfaceObservation(Surface.GITHUB, True, "CI_GREEN", "oldsha")
    decision = reconcile(cp, obs)
    assert decision.state == SyncState.BLOCKED
    assert decision.stale == (Surface.GITHUB,)


def test_synced_checkpoint_without_completion_is_review_ready():
    cp = CheckpointEnvelope("PAS-185", "IMPLEMENTED", "abc123", request_complete=False)
    decision = reconcile(cp, observations("abc123"))
    assert decision.state == SyncState.READY_FOR_REVIEW
    assert decision.can_complete is False


# --- PAS-250: per-source freshness, drift/conflict readback, ownership-safe fanout ---

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class FakeAdapter:
    def __init__(self, surface, *, head="abc123", status="OK", exc=None, report_as=None):
        self.surface = surface
        self.head = head
        self.status = status
        self.exc = exc
        self.report_as = report_as or surface
        self.calls = 0

    def read_state(self, mission_id):
        raise NotImplementedError

    def publish_checkpoint(self, checkpoint):
        self.calls += 1
        if self.exc:
            raise self.exc
        return SurfaceObservation(self.report_as, True, self.status, self.head)


def fresh(surface, head="abc123", status="OK", age=0):
    return SurfaceObservation(surface, True, status, head, observed_at=NOW - timedelta(seconds=age))


def test_unreachable_source_does_not_collapse_reachable_reconciliation():
    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    obs = [
        fresh(Surface.LOCAL),
        fresh(Surface.GITHUB),
        fresh(Surface.LINEAR),
        SurfaceObservation(Surface.NOTION, False, "ERROR", error="URLError", observed_at=NOW),
    ]
    decision = reconcile(cp, obs, now=NOW, max_age=timedelta(minutes=15))
    assert decision.state == SyncState.BLOCKED
    assert decision.missing == (Surface.NOTION,)
    assert decision.reconciled == (Surface.LOCAL, Surface.LINEAR, Surface.GITHUB)
    health = {s.surface: s for s in decision.sources}
    assert health[Surface.NOTION].freshness == SourceFreshness.UNREACHABLE
    assert health[Surface.NOTION].last_error_at == NOW
    assert health[Surface.LOCAL].freshness == SourceFreshness.FRESH
    assert health[Surface.LOCAL].last_success_at == NOW


def test_old_observation_is_expired_not_reconciled():
    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    obs = [fresh(s) for s in (Surface.LOCAL, Surface.LINEAR, Surface.GITHUB)]
    obs.append(fresh(Surface.NOTION, age=3600))
    decision = reconcile(cp, obs, now=NOW, max_age=timedelta(minutes=15))
    assert decision.state == SyncState.BLOCKED
    assert decision.expired == (Surface.NOTION,)
    assert Surface.NOTION not in decision.reconciled
    assert {s.surface: s for s in decision.sources}[Surface.NOTION].age_seconds == 3600


def test_drift_is_per_source_and_divergent_heads_are_a_conflict():
    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    obs = [
        fresh(Surface.LOCAL, head="local999"),
        fresh(Surface.GITHUB, head="remote888"),
        fresh(Surface.LINEAR),
        fresh(Surface.NOTION),
    ]
    decision = reconcile(cp, obs, now=NOW)
    assert decision.drift == (Surface.LOCAL, Surface.GITHUB)
    assert decision.reconciled == (Surface.LINEAR, Surface.NOTION)
    assert [c.kind for c in decision.conflicts] == [ConflictKind.HEAD_SHA]
    assert decision.conflicts[0].surfaces == (Surface.LOCAL, Surface.GITHUB)


def test_single_drift_is_not_a_conflict():
    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    obs = observations("abc123")
    obs[2] = SurfaceObservation(Surface.GITHUB, True, "OPEN", "oldsha")
    decision = reconcile(cp, obs)
    assert decision.drift == (Surface.GITHUB,)
    assert decision.conflicts == ()


def test_terminal_surface_status_conflicts_with_active_checkpoint():
    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    obs = observations("abc123")
    obs[2] = SurfaceObservation(Surface.GITHUB, True, "CLOSED", "abc123")
    decision = reconcile(cp, obs)
    assert decision.state == SyncState.BLOCKED
    assert decision.conflicts[0].kind == ConflictKind.STATUS
    assert decision.conflicts[0].surfaces == (Surface.GITHUB,)


def test_decision_readback_is_json_serialisable():
    import json

    cp = CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123")
    decision = reconcile(cp, [fresh(Surface.LOCAL)], now=NOW)
    payload = json.loads(json.dumps(decision.to_dict()))
    assert payload["missing"] == ["linear", "github", "notion"]
    assert payload["sources"][0]["freshness"] == "FRESH"


def test_fanout_isolates_failing_adapter_and_stamps_timestamps():
    adapters = [
        FakeAdapter(Surface.LOCAL),
        FakeAdapter(Surface.LINEAR, exc=TimeoutError()),
        FakeAdapter(Surface.GITHUB),
        FakeAdapter(Surface.NOTION),
    ]
    fanout = CheckpointFanout(adapters, clock=lambda: NOW)
    decision = fanout.publish(CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123"))
    assert all(adapter.calls == 1 for adapter in adapters)
    assert decision.missing == (Surface.LINEAR,)
    assert decision.reconciled == (Surface.LOCAL, Surface.GITHUB, Surface.NOTION)
    linear = {s.surface: s for s in decision.sources}[Surface.LINEAR]
    assert linear.error == "TimeoutError"
    assert linear.last_error_at == NOW


def test_fanout_refuses_non_owner_without_writing():
    adapters = [FakeAdapter(s) for s in (Surface.LOCAL, Surface.GITHUB)]
    fanout = CheckpointFanout(adapters, owner_check=lambda m, a: a == "writer-1")
    decision = fanout.publish(
        CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123"), agent_id="intruder"
    )
    assert decision.state == SyncState.BLOCKED
    assert decision.conflicts[0].kind == ConflictKind.OWNERSHIP
    assert all(adapter.calls == 0 for adapter in adapters)

    decision = fanout.publish(
        CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123"),
        agent_id="writer-1",
        required=(Surface.LOCAL, Surface.GITHUB),
    )
    assert decision.state == SyncState.READY_FOR_REVIEW
    assert all(adapter.calls == 1 for adapter in adapters)


def test_fanout_discards_cross_surface_report():
    adapters = [
        FakeAdapter(Surface.LOCAL),
        FakeAdapter(Surface.NOTION, report_as=Surface.GITHUB, head="evil"),
    ]
    decision = CheckpointFanout(adapters, clock=lambda: NOW).publish(
        CheckpointEnvelope("PAS-250", "IMPLEMENTED", "abc123"),
        required=(Surface.LOCAL, Surface.NOTION),
    )
    assert decision.conflicts[0].kind == ConflictKind.SURFACE_MISMATCH
    assert decision.missing == (Surface.NOTION,)
    assert Surface.GITHUB not in {s.surface for s in decision.sources}
    assert decision.reconciled == (Surface.LOCAL,)


def test_fanout_rejects_duplicate_surface_adapters():
    with pytest.raises(ValueError):
        CheckpointFanout([FakeAdapter(Surface.LOCAL), FakeAdapter(Surface.LOCAL)])
