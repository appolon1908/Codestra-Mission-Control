import pytest

from mission_control.approval_engine import (
    ActorKind,
    ApprovalContext,
    ApprovalDenied,
    ApprovalEngine,
)
from mission_control.lease import LeaseManager
from mission_control.models import ApprovalLevel, Mission
from mission_control.store import MissionStore


def store_for(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-186", "repo", "goal"))
    return store


def test_writer_cannot_self_approve_merge(tmp_path):
    store = store_for(tmp_path)
    LeaseManager(store).claim("PAS-186", "writer-1")
    engine = ApprovalEngine(store)
    with pytest.raises(ApprovalDenied):
        engine.approve(
            "PAS-186",
            ApprovalLevel.MERGE,
            ApprovalContext("writer-1", ActorKind.AGENT, ci_green=True),
        )


def test_independent_reviewer_can_approve_merge_with_green_ci(tmp_path, authorize_merge):
    store = store_for(tmp_path)
    LeaseManager(store).claim("PAS-186", "writer-1")
    engine = ApprovalEngine(store)
    result = engine.approve(
        "PAS-186",
        ApprovalLevel.MERGE,
        ApprovalContext("reviewer-1", ActorKind.AGENT, ci_green=True),
    )
    assert result.recorded is True
    # A recorded approval alone no longer authorizes merge: exact-head coordination is required.
    assert engine.can_execute("PAS-186", "merge") is False
    authorize_merge(store, "PAS-186", claim=False)
    assert engine.can_execute("PAS-186", "merge") is True


def test_staging_requires_certification(tmp_path):
    store = store_for(tmp_path)
    engine = ApprovalEngine(store)
    with pytest.raises(ApprovalDenied):
        engine.approve(
            "PAS-186",
            ApprovalLevel.STAGING_MUTATION,
            ApprovalContext("reviewer-1", ActorKind.AGENT, ci_green=True),
        )


def test_production_requires_explicit_human(tmp_path, authorize_merge):
    store = store_for(tmp_path)
    engine = ApprovalEngine(store)
    with pytest.raises(ApprovalDenied):
        engine.approve(
            "PAS-186",
            ApprovalLevel.PRODUCTION_EFFECT,
            ApprovalContext(
                "reviewer-1",
                ActorKind.AGENT,
                ci_green=True,
                certification_passed=True,
                explicit_production_confirmation=True,
            ),
        )

    result = engine.approve(
        "PAS-186",
        ApprovalLevel.PRODUCTION_EFFECT,
        ApprovalContext(
            "ralph",
            ActorKind.HUMAN,
            ci_green=True,
            certification_passed=True,
            explicit_production_confirmation=True,
        ),
    )
    assert result.recorded is True
    assert engine.can_execute("PAS-186", "production_effect") is False
    authorize_merge(store, "PAS-186")
    assert engine.can_execute("PAS-186", "production_effect") is True
