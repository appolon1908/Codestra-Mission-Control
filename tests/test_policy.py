from mission_control.models import AgentRole, ApprovalGate, ApprovalLevel, Mission
from mission_control.policy import ApprovalPolicy
from mission_control.store import MissionStore


def test_merge_requires_exact_sha_merge_coordinator_authorization(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    head = "a" * 40
    store.upsert_mission(Mission("PAS-1", "repo", "goal", head_sha=head))
    policy = ApprovalPolicy(store)

    assert policy.evaluate("PAS-1", "merge").allowed is False

    # Legacy numeric merge approval is intentionally insufficient.
    store.record_approval("PAS-1", ApprovalLevel.MERGE, "reviewer")
    assert policy.evaluate("PAS-1", "merge").allowed is False

    store.record_sha_approval(
        "PAS-1",
        gate=ApprovalGate.MERGE_AUTHORIZATION,
        actor="merge-coordinator",
        actor_role=AgentRole.MERGE_COORDINATOR,
        head_sha=head,
        evidence={"review": "PASS", "verification": "PASS"},
    )
    assert policy.evaluate("PAS-1", "merge").allowed is True


def test_production_is_not_implied_by_exact_sha_merge_authorization(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    head = "b" * 40
    store.upsert_mission(Mission("PAS-2", "repo", "goal", head_sha=head))
    store.record_sha_approval(
        "PAS-2",
        gate=ApprovalGate.MERGE_AUTHORIZATION,
        actor="merge-coordinator",
        actor_role=AgentRole.MERGE_COORDINATOR,
        head_sha=head,
        evidence={},
    )
    decision = ApprovalPolicy(store).evaluate("PAS-2", "production_effect")
    assert decision.allowed is False
    assert decision.required == ApprovalLevel.PRODUCTION_EFFECT
