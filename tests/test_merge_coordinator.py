from __future__ import annotations

import pytest

from mission_control.lease import LeaseManager
from mission_control.merge_coordinator import MergeCandidate, MergeCoordinator
from mission_control.models import (
    AgentRole,
    ApprovalGate,
    ConflictClass,
    MergeQueueState,
    Mission,
    MissionStatus,
)
from mission_control.store import MissionStore

HEAD_A = "a" * 40
HEAD_B = "b" * 40
BASE = "c" * 40
TARGET = "d" * 40


def store_for(tmp_path) -> MissionStore:
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission(
            "PAS-258",
            "repo",
            "merge coordinator",
            head_sha=HEAD_A,
            base_sha=BASE,
        )
    )
    return store


def candidate(**overrides) -> MergeCandidate:
    values = {
        "mission_id": "PAS-258",
        "repository": "owner/repo",
        "pr_number": 42,
        "head_sha": HEAD_A,
        "base_sha": BASE,
        "target_sha": TARGET,
        "mergeable": True,
        "ci_green": True,
        "protected_rules_allow": True,
        "base_current": True,
        "control_sync_current": True,
    }
    values.update(overrides)
    return MergeCandidate(**values)


def test_writer_cannot_record_own_review_or_verification(tmp_path):
    store = store_for(tmp_path)
    LeaseManager(store).claim("PAS-258", "builder-1", role=AgentRole.WRITER)
    coordinator = MergeCoordinator(store)

    with pytest.raises(ValueError, match="writer cannot review"):
        coordinator.record_review_acceptance(
            "PAS-258",
            actor="builder-1",
            head_sha=HEAD_A,
        )

    with pytest.raises(ValueError, match="writer cannot verify"):
        coordinator.record_verification_acceptance(
            "PAS-258",
            actor="builder-1",
            head_sha=HEAD_A,
        )


def test_approvals_are_bound_to_exact_sha_and_staled_on_head_change(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer-1",
        head_sha=HEAD_A,
    )
    assert (
        store.latest_valid_sha_approval("PAS-258", ApprovalGate.REVIEW, HEAD_A)
        is not None
    )

    store.record_checkpoint(
        "PAS-258",
        "builder-1",
        "PUSHED",
        head_sha=HEAD_B,
        dirty_count=0,
        tests={"unit": "PASS"},
        blockers=[],
        next_task_requested=False,
    )

    assert (
        store.latest_valid_sha_approval("PAS-258", ApprovalGate.REVIEW, HEAD_A)
        is None
    )
    events = [row["event_type"] for row in store.events("PAS-258")]
    assert "SHA_APPROVALS_INVALIDATED" in events


def test_missing_review_redispatches_reviewer(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)

    result = coordinator.evaluate(candidate())

    assert result.allowed is False
    assert result.state is MergeQueueState.WAITING_REVIEW
    assert result.required_role is AgentRole.REVIEWER
    requests = store.pending_dispatch_requests(role=AgentRole.REVIEWER)
    assert len(requests) == 1
    assert requests[0]["head_sha"] == HEAD_A
    assert store.get_mission("PAS-258")["status"] == MissionStatus.IN_REVIEW.value


def test_review_then_missing_verification_redispatches_verifier(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer-1",
        head_sha=HEAD_A,
    )

    result = coordinator.evaluate(candidate())

    assert result.allowed is False
    assert result.state is MergeQueueState.WAITING_VERIFICATION
    assert result.required_role is AgentRole.VERIFIER
    requests = store.pending_dispatch_requests(role=AgentRole.VERIFIER)
    assert len(requests) == 1


def test_reviewer_and_verifier_must_be_independent(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="agent-1",
        head_sha=HEAD_A,
    )

    with pytest.raises(ValueError, match="independent actors"):
        coordinator.record_verification_acceptance(
            "PAS-258",
            actor="agent-1",
            head_sha=HEAD_A,
        )


def test_semantic_conflict_fails_closed_and_redispatches_builder(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)

    result = coordinator.evaluate(
        candidate(
            conflict_class=ConflictClass.SEMANTIC,
            conflict_files=("src/api.py", "contracts/openapi.yaml"),
            conflict_summary="API contract overlap",
        )
    )

    assert result.allowed is False
    assert result.state is MergeQueueState.BLOCKED_CONFLICT
    assert result.required_role is AgentRole.WRITER
    conflict = store.latest_open_conflict("PAS-258")
    assert conflict is not None
    assert conflict["conflict_class"] == ConflictClass.SEMANTIC.value
    assert store.get_mission("PAS-258")["status"] == MissionStatus.CONFLICT.value


def test_unknown_conflict_does_not_guess_or_dispatch_writer(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)

    result = coordinator.evaluate(
        candidate(
            conflict_class=ConflictClass.UNKNOWN,
            conflict_files=("src/unknown.py",),
        )
    )

    assert result.allowed is False
    assert result.required_role is None
    assert store.pending_dispatch_requests(role=AgentRole.WRITER) == []


def test_all_exact_sha_gates_produce_merge_ready(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer-1",
        head_sha=HEAD_A,
        evidence={"review": "PASS"},
    )
    coordinator.record_verification_acceptance(
        "PAS-258",
        actor="verifier-1",
        head_sha=HEAD_A,
        evidence={"tests": "PASS"},
    )

    result = coordinator.evaluate(candidate())

    assert result.allowed is True
    assert result.state is MergeQueueState.READY
    assert store.get_mission("PAS-258")["status"] == MissionStatus.MERGE_READY.value
    approval = store.latest_valid_sha_approval(
        "PAS-258",
        ApprovalGate.MERGE_AUTHORIZATION,
        HEAD_A,
    )
    assert approval is not None
    assert approval["actor_role"] == AgentRole.MERGE_COORDINATOR.value


def test_dependency_blocks_until_exact_dependency_merge_exists(tmp_path):
    store = store_for(tmp_path)
    store.upsert_mission(
        Mission(
            "PAS-DEP",
            "dep-repo",
            "dependency",
            head_sha=HEAD_B,
            base_sha=BASE,
        )
    )
    coordinator = MergeCoordinator(store)

    store.enqueue_merge(
        "PAS-258",
        repository="owner/repo",
        pr_number=42,
        head_sha=HEAD_A,
        base_sha=BASE,
        target_sha=TARGET,
    )
    store.add_merge_dependency("PAS-258", "PAS-DEP")
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer-1",
        head_sha=HEAD_A,
    )
    coordinator.record_verification_acceptance(
        "PAS-258",
        actor="verifier-1",
        head_sha=HEAD_A,
    )

    result = coordinator.evaluate(candidate())
    assert result.state is MergeQueueState.WAITING_DEPENDENCY

    store.enqueue_merge(
        "PAS-DEP",
        repository="owner/dep",
        pr_number=7,
        head_sha=HEAD_B,
        base_sha=BASE,
        target_sha=TARGET,
    )
    store.record_merge_result(
        "PAS-DEP",
        merge_sha="e" * 40,
        merge_method="squash",
        actor="merge-coordinator",
    )

    result = coordinator.evaluate(candidate())
    assert result.allowed is True


def test_record_merge_persists_merge_sha_and_status(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer-1",
        head_sha=HEAD_A,
    )
    coordinator.record_verification_acceptance(
        "PAS-258",
        actor="verifier-1",
        head_sha=HEAD_A,
    )
    assert coordinator.evaluate(candidate()).allowed

    result_id = coordinator.record_merge(
        "PAS-258",
        merge_sha="f" * 40,
        merge_method="squash",
    )

    assert result_id > 0
    queue = store.get_merge_queue_item("PAS-258")
    assert queue["state"] == MergeQueueState.MERGED.value
    assert store.get_mission("PAS-258")["status"] == MissionStatus.MERGED.value


def test_conflict_classifier_fails_closed_without_explicit_hint():
    assert MergeCoordinator.classify_conflict([]) is ConflictClass.NONE
    assert (
        MergeCoordinator.classify_conflict(["docs/a.md"], mechanical_hint=True)
        is ConflictClass.MECHANICAL
    )
    assert (
        MergeCoordinator.classify_conflict(["src/api.py"], semantic_hint=True)
        is ConflictClass.SEMANTIC
    )
    assert (
        MergeCoordinator.classify_conflict(
            ["contracts/openapi.yaml"],
            cross_repo=True,
        )
        is ConflictClass.CROSS_REPO
    )
    assert (
        MergeCoordinator.classify_conflict(["src/api.py"])
        is ConflictClass.UNKNOWN
    )


def test_unresolved_conflict_record_blocks_even_if_candidate_reports_none(tmp_path):
    store = store_for(tmp_path)
    coordinator = MergeCoordinator(store)
    conflict_id = store.record_conflict(
        "PAS-258",
        repository="owner/repo",
        pr_number=42,
        head_sha=HEAD_A,
        base_sha=BASE,
        conflict_class=ConflictClass.SEMANTIC,
        files=["src/api.py"],
        summary="semantic overlap",
    )
    result = coordinator.evaluate(candidate(conflict_class=ConflictClass.NONE))
    assert result.allowed is False
    assert result.state is MergeQueueState.BLOCKED_CONFLICT
    assert str(conflict_id) in result.reason

    store.resolve_conflict(conflict_id, actor="reviewer-1")
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer-1",
        head_sha=HEAD_A,
    )
    coordinator.record_verification_acceptance(
        "PAS-258",
        actor="verifier-1",
        head_sha=HEAD_A,
    )
    assert coordinator.evaluate(candidate()).allowed is True
