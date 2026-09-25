import pytest

from mission_control.lease import LeaseManager
from mission_control.merge_coordinator import (
    EvidenceRole,
    EvidenceVerdict,
    MergeCoordinator,
    PullRequestSnapshot,
)

HEAD_A = "a" * 40
HEAD_B = "b" * 40
BASE = "c" * 40


def clean_snapshot(head_sha=HEAD_A, **overrides):
    payload = {
        "pr_number": 42,
        "head_sha": head_sha,
        "head_branch": "impl/feature",
        "base_branch": "main",
        "base_sha": BASE,
        "target_sha": BASE,
        "mergeable": True,
        "required_checks": ["ci"],
        "checks": {"ci": "success"},
        "protected_rules_allow": True,
    }
    payload.update(overrides)
    return PullRequestSnapshot.from_dict(payload)


def prepare_merge(store, mission_id, *, head_sha=HEAD_A, writer="builder-1", claim=True):
    """Drive a mission through builder head, checkpoint, review and verification."""
    coordinator = MergeCoordinator(store)
    if claim:
        LeaseManager(store).claim(mission_id, writer)
    coordinator.record_head(mission_id, head_sha, writer)
    store.record_checkpoint(
        mission_id,
        writer,
        "PUSHED",
        head_sha=head_sha,
        dirty_count=0,
        tests={"pytest": "pass"},
        blockers=[],
        next_task_requested=False,
    )
    coordinator.record_evidence(
        mission_id,
        role=EvidenceRole.REVIEWER,
        head_sha=head_sha,
        actor="reviewer-1",
        verdict=EvidenceVerdict.ACCEPTED,
    )
    coordinator.record_evidence(
        mission_id,
        role=EvidenceRole.VERIFIER,
        head_sha=head_sha,
        actor="verifier-1",
        verdict=EvidenceVerdict.ACCEPTED,
    )
    return coordinator


@pytest.fixture
def authorize_merge():
    def _authorize(store, mission_id, *, claim=True):
        coordinator = prepare_merge(store, mission_id, claim=claim)
        decision = coordinator.evaluate(mission_id, clean_snapshot())
        assert decision.merge_allowed, decision.reasons
        return decision

    return _authorize
