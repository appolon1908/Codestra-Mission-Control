
from mission_control.models import ApprovalLevel, Mission
from mission_control.policy import ApprovalPolicy
from mission_control.store import MissionStore


def test_merge_requires_merge_approval(tmp_path, authorize_merge):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-1", "repo", "goal"))
    policy = ApprovalPolicy(store)
    assert policy.evaluate("PAS-1", "merge").allowed is False
    store.record_approval("PAS-1", ApprovalLevel.MERGE, "reviewer")
    legacy = policy.evaluate("PAS-1", "merge")
    assert legacy.allowed is False
    assert "merge-coordinator authorization" in legacy.reason
    authorize_merge(store, "PAS-1")
    assert policy.evaluate("PAS-1", "merge").allowed is True


def test_production_is_not_implied_by_merge(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-2", "repo", "goal"))
    store.record_approval("PAS-2", ApprovalLevel.MERGE, "reviewer")
    decision = ApprovalPolicy(store).evaluate("PAS-2", "production_effect")
    assert decision.allowed is False
    assert decision.required == ApprovalLevel.PRODUCTION_EFFECT
