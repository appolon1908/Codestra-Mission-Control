import pytest

from mission_control.approval_engine import (
    ActorKind,
    ApprovalContext,
    ApprovalDenied,
    ApprovalEngine,
)
from mission_control.lease import LeaseManager
from mission_control.models import AgentRole, ApprovalGate, ApprovalLevel, Mission
from mission_control.store import MissionStore


def store_for(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-186", "repo", "goal", head_sha="a" * 40))
    return store


def authorize_exact_head_merge(store):
    store.record_sha_approval(
        "PAS-186",
        gate=ApprovalGate.MERGE_AUTHORIZATION,
        actor="merge-coordinator",
        actor_role=AgentRole.MERGE_COORDINATOR,
        head_sha="a" * 40,
        evidence={"gate": "PASS"},
    )


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


def test_direct_merge_approval_is_disabled_in_favor_of_sha_bound_coordinator(tmp_path):
    store = store_for(tmp_path)
    LeaseManager(store).claim("PAS-186", "writer-1")
    engine = ApprovalEngine(store)
    with pytest.raises(ApprovalDenied, match="coordinator-only"):
        engine.approve(
            "PAS-186",
            ApprovalLevel.MERGE,
            ApprovalContext("reviewer-1", ActorKind.AGENT, ci_green=True),
        )
    assert engine.can_execute("PAS-186", "merge") is False


def test_staging_requires_certification(tmp_path):
    store = store_for(tmp_path)
    authorize_exact_head_merge(store)
    engine = ApprovalEngine(store)
    with pytest.raises(ApprovalDenied):
        engine.approve(
            "PAS-186",
            ApprovalLevel.STAGING_MUTATION,
            ApprovalContext("reviewer-1", ActorKind.AGENT, ci_green=True),
        )


def test_production_requires_explicit_human(tmp_path):
    store = store_for(tmp_path)
    authorize_exact_head_merge(store)
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
    assert engine.can_execute("PAS-186", "production_effect") is True
