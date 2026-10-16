"""Caller reports and cached local decisions cannot establish controller authority."""

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FixtureController, clean_snapshot, prepare_merge

from mission_control import governed_gates
from mission_control.lease import LeaseManager
from mission_control.merge_coordinator import PullRequestSnapshot
from mission_control.models import Mission, MissionStatus
from mission_control.policy import ApprovalPolicy
from mission_control.store import CompletionBlocked, MissionStore


def prepared_store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("GATES-1", "Codestra-Mission-Control", "governed gates"))
    coordinator = prepare_merge(store, "GATES-1", governed=False)
    return store, coordinator


def test_green_local_evidence_cannot_clear_unconfigured_controller(tmp_path):
    store, coordinator = prepared_store(tmp_path)
    decision = coordinator.evaluate("GATES-1", clean_snapshot())
    assert decision.merge_allowed is False
    assert {"ROUTING_UNVERIFIED", "CONTROLLER_VERIFICATION_UNCONFIGURED"} <= set(
        decision.reasons
    )
    assert store.merge_authorization("GATES-1")["authorized"] is False


def ready_store(tmp_path):
    store, _ = prepared_store(tmp_path)
    coordinator = prepare_merge(store, "GATES-1", claim=False)
    return store, coordinator


class ObservationProvider:
    def __init__(self, transform):
        self.transform = transform

    def verify(self, binding):
        return self.transform(FixtureController().verify(binding))


@pytest.mark.parametrize("field,value", [
    ("mission_id", "OTHER"), ("product", "Other-Product"),
    ("repository", "other-owner/other-repo"), ("pr_number", 99),
    ("head_branch", "atomic/other"), ("base_branch", "section/other"),
    ("head_sha", "d" * 40), ("base_sha", "e" * 40), ("target_sha", "f" * 40),
])
def test_observation_for_different_binding_cannot_clear_gates(tmp_path, field, value):
    store, coordinator = ready_store(tmp_path)
    store.controller_gate_provider = ObservationProvider(
        lambda obs: replace(obs, binding=replace(obs.binding, **{field: value}))
    )
    decision = coordinator.evaluate("GATES-1", clean_snapshot())
    assert decision.merge_allowed is False
    assert "CONTROLLER_BINDING_MISMATCH" in decision.reasons


@pytest.mark.parametrize("transform,reason", [
    (lambda obs: asdict(obs), "CONTROLLER_OBSERVATION_INVALID"),
    (lambda obs: replace(obs, controller_verified="PASS"), "CONTROLLER_OBSERVATION_INVALID"),
    (lambda obs: replace(obs, routing_verified=1), "CONTROLLER_OBSERVATION_INVALID"),
    (lambda obs: replace(obs, observed_at=datetime.now(UTC).replace(tzinfo=None)),
     "CONTROLLER_OBSERVATION_INVALID"),
    (lambda obs: replace(obs, expires_at=datetime.now(UTC)-timedelta(seconds=1)),
     "CONTROLLER_OBSERVATION_STALE"),
    (lambda obs: replace(obs, observed_at=datetime.now(UTC)+timedelta(minutes=1)),
     "CONTROLLER_OBSERVATION_STALE"),
    (lambda obs: replace(obs, expires_at=obs.observed_at+timedelta(minutes=6)),
     "CONTROLLER_OBSERVATION_STALE"),
])
def test_malformed_or_stale_observations_fail_closed(tmp_path, transform, reason):
    store, coordinator = ready_store(tmp_path)
    store.controller_gate_provider = ObservationProvider(transform)
    decision = coordinator.evaluate("GATES-1", clean_snapshot())
    assert decision.merge_allowed is False
    assert reason in decision.reasons


@pytest.mark.parametrize("flag,gate,reason", [
    ("routing_verified", "ROUTING", "ROUTING_UNVERIFIED"),
    ("controller_verified", "CONTROLLER", "CONTROLLER_UNVERIFIED"),
])
def test_registered_routing_and_controller_gates_are_separate(tmp_path, flag, gate, reason):
    store, coordinator = ready_store(tmp_path)
    store.controller_gate_provider = ObservationProvider(lambda obs: replace(obs, **{flag: False}))
    decision = coordinator.evaluate("GATES-1", clean_snapshot())
    assert decision.merge_allowed is False
    assert decision.next_gate.value == gate
    assert reason in decision.reasons


def test_provider_failure_never_exposes_credential_exception(tmp_path):
    store, coordinator = ready_store(tmp_path)

    def failure(obs):
        raise RuntimeError("secret connection string must not escape")

    store.controller_gate_provider = ObservationProvider(failure)
    decision = coordinator.evaluate("GATES-1", clean_snapshot())
    assert "CONTROLLER_VERIFICATION_UNAVAILABLE" in decision.reasons
    assert "secret" not in str(decision.as_dict())


def test_cached_ready_decision_cannot_survive_authority_revocation(tmp_path):
    store, coordinator = ready_store(tmp_path)
    assert coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed
    assert store.merge_authorization("GATES-1")["authorized"]
    store.controller_gate_provider = None
    assert store.merge_authorization("GATES-1")["authorized"] is False
    assert coordinator.latest_decision("GATES-1")["merge_allowed"] is False
    assert ApprovalPolicy(store).evaluate("GATES-1", "merge").allowed is False
    with pytest.raises(CompletionBlocked):
        store.set_status("GATES-1", MissionStatus.COMPLETE)
    with pytest.raises(CompletionBlocked):
        LeaseManager(store).release("GATES-1", "builder-1", next_status=MissionStatus.COMPLETE)
    assert LeaseManager(store).current("GATES-1")["agent_id"] == "builder-1"
    # Reopening the ledger cannot restore a privileged provider from saved JSON.
    reopened = MissionStore(store.path)
    assert reopened.merge_authorization("GATES-1")["authorized"] is False
    assert coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed is False
    assert store.get_mission("GATES-1")["status"] == "IN_REVIEW"


def test_changed_repository_cannot_rebind_old_decision(tmp_path):
    store, coordinator = ready_store(tmp_path)
    assert coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed
    with store.connection() as conn:
        conn.execute("UPDATE repositories SET full_name='other/repo'")
    result = store.merge_authorization("GATES-1")
    assert result["authorized"] is False
    assert "CONTROLLER_BINDING_UNRECORDED_OR_CHANGED" in result["reasons"]


@pytest.mark.parametrize("during_evaluation", [False, True])
def test_head_change_during_provider_read_cannot_authorize(tmp_path, during_evaluation):
    store, coordinator = ready_store(tmp_path)
    if not during_evaluation:
        assert coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed

    def advance_head(obs):
        coordinator.record_head("GATES-1", "b" * 40, "builder-1")
        return obs

    store.controller_gate_provider = ObservationProvider(advance_head)
    allowed = (coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed
               if during_evaluation else store.merge_authorization("GATES-1")["authorized"])
    assert allowed is False


def test_missing_registry_identity_cannot_use_request_repository(tmp_path):
    store, coordinator = ready_store(tmp_path)
    with store.connection() as conn:
        conn.execute("DELETE FROM repositories")
    payload = asdict(clean_snapshot()) | {"repository": "fixture-owner/Codestra-Mission-Control"}
    decision = coordinator.evaluate("GATES-1", PullRequestSnapshot.from_dict(payload))
    assert decision.merge_allowed is False
    assert "CONTROLLER_BINDING_INVALID" in decision.reasons


def test_historical_ready_without_persisted_binding_cannot_be_upgraded(tmp_path):
    store, coordinator = ready_store(tmp_path)
    assert coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed
    with store.connection() as conn:
        row = conn.execute("SELECT id, snapshot_json FROM merge_decisions").fetchone()
        snapshot = json.loads(row["snapshot_json"])
        snapshot.pop("_governed_binding")
        conn.execute("UPDATE merge_decisions SET snapshot_json=? WHERE id=?",
                     (json.dumps(snapshot), row["id"]))
    result = store.merge_authorization("GATES-1")
    assert result["authorized"] is False
    assert "CONTROLLER_BINDING_UNRECORDED_OR_CHANGED" in result["reasons"]


def test_decision_report_cannot_borrow_newer_decisions_authorization(tmp_path, monkeypatch):
    store, coordinator = ready_store(tmp_path)
    first = coordinator.evaluate("GATES-1", clean_snapshot())
    original = store.latest_merge_decision
    advance = True

    def read_then_advance(mission_id):
        nonlocal advance
        row = original(mission_id)
        if advance:
            advance = False
            # Deterministically place a concurrent evaluation between the report's
            # historical row read and its current authorization lookup.
            coordinator.evaluate(mission_id, clean_snapshot())
        return row

    monkeypatch.setattr(store, "latest_merge_decision", read_then_advance)
    report = coordinator.latest_decision("GATES-1")
    assert report["decision_id"] == first.decision_id
    assert report["merge_allowed"] is False


@pytest.mark.parametrize("during_verification", [False, True])
def test_current_mission_branch_changes_revoke_authorization(tmp_path, during_verification):
    store, coordinator = ready_store(tmp_path)
    with store.connection() as conn:
        conn.execute("UPDATE missions SET branch='impl/feature'")
    assert coordinator.evaluate("GATES-1", clean_snapshot()).merge_allowed

    def change_branch(obs):
        with store.connection() as conn:
            conn.execute("UPDATE missions SET branch='impl/other'")
        return obs

    if during_verification:
        store.controller_gate_provider = ObservationProvider(change_branch)
    else:
        change_branch(None)
    result = store.merge_authorization("GATES-1")
    assert result["authorized"] is False
    assert "MERGE_DECISION_BRANCH_CHANGED" in result["reasons"]


def test_dst_fallback_cannot_extend_controller_evidence_lifetime(tmp_path, monkeypatch):
    store, coordinator = ready_store(tmp_path)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 1, 6, 30, tzinfo=UTC).astimezone(tz)

    zone = ZoneInfo("America/New_York")
    observed = FixedClock(2026, 11, 1, 1, 59, tzinfo=zone, fold=0)  # 05:59 UTC
    expires = FixedClock(2026, 11, 1, 2, 4, tzinfo=zone)  # 07:04 UTC: 65 minutes later
    monkeypatch.setattr(governed_gates, "datetime", FixedClock)
    store.controller_gate_provider = ObservationProvider(
        lambda obs: replace(obs, observed_at=observed, expires_at=expires)
    )
    decision = coordinator.evaluate("GATES-1", clean_snapshot())
    assert decision.merge_allowed is False
    assert "CONTROLLER_OBSERVATION_STALE" in decision.reasons


def test_author_json_cannot_supply_routing_or_controller_authority(tmp_path):
    store, coordinator = prepared_store(tmp_path)
    payload = asdict(clean_snapshot())
    payload.update(
        routing_verified=True,
        controller_verified=True,
        controller_certification={"status": "PASS", "actor": "controller"},
    )
    decision = coordinator.evaluate("GATES-1", PullRequestSnapshot.from_dict(payload))
    assert decision.merge_allowed is False
    assert "CONTROLLER_VERIFICATION_UNCONFIGURED" in decision.reasons
    assert store.merge_authorization("GATES-1")["authorized"] is False
