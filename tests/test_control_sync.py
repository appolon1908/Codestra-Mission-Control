from mission_control.control_sync import (
    CheckpointEnvelope,
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


def test_checkpoint_status_drift_blocks_completion():
    cp = CheckpointEnvelope("PAS-250", "CERTIFIED", "abc123", request_complete=True)
    obs = [
        SurfaceObservation(
            Surface.LOCAL,
            True,
            "OK",
            "abc123",
            checkpoint_status="CERTIFIED",
        ),
        SurfaceObservation(
            Surface.LINEAR,
            True,
            "In Progress",
            "abc123",
            checkpoint_status="IMPLEMENTED",
        ),
        SurfaceObservation(
            Surface.GITHUB,
            True,
            "OPEN",
            "abc123",
            checkpoint_status="CERTIFIED",
        ),
        SurfaceObservation(
            Surface.NOTION,
            True,
            "AVAILABLE",
            "abc123",
            checkpoint_status="CERTIFIED",
        ),
    ]

    decision = reconcile(cp, obs)

    assert decision.state == SyncState.BLOCKED
    assert decision.can_complete is False
    assert decision.stale == (Surface.LINEAR,)
    assert "stale=linear" in decision.reason
