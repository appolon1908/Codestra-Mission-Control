import sqlite3

import pytest
from conftest import BASE, HEAD_A, HEAD_B, clean_snapshot, prepare_merge

from mission_control.lease import LeaseManager
from mission_control.merge_coordinator import (
    ConflictClass,
    EvidenceRejected,
    EvidenceRole,
    EvidenceVerdict,
    Gate,
    MergeCoordinator,
    Verdict,
    classify_conflicts,
    classify_path,
)
from mission_control.models import Mission, MissionStatus
from mission_control.policy import ApprovalPolicy
from mission_control.store import CompletionBlocked, MissionStore

MISSION = "PAS-258"


@pytest.fixture
def store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission(MISSION, "Codestra-Mission-Control", "merge coordinator"))
    return store


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", ConflictClass.MECHANICAL),
        ("frontend/package-lock.json", ConflictClass.MECHANICAL),
        ("uv.lock", ConflictClass.MECHANICAL),
        ("src/api/generated/client.ts", ConflictClass.MECHANICAL),
        ("src/api/generated/migrations/0002.sql", ConflictClass.SEMANTIC),
        ("docs/api/openapi.yaml", ConflictClass.SEMANTIC),
        ("docs/runbook.md", ConflictClass.MECHANICAL),
        ("src/mission_control/store.py", ConflictClass.SEMANTIC),
        ("db/migrations/0001_init.sql", ConflictClass.SEMANTIC),
        ("schemas/handoff.schema.json", ConflictClass.SEMANTIC),
        (".github/workflows/ci.yml", ConflictClass.UNSAFE),
        ("CODEOWNERS", ConflictClass.UNSAFE),
        ("assets/logo.png", ConflictClass.UNSAFE),
        ("../outside.py", ConflictClass.UNSAFE),
        ("/etc/passwd", ConflictClass.UNSAFE),
        ("C:/Windows/x.py", ConflictClass.UNSAFE),
    ],
)
def test_classify_path(path, expected):
    assert classify_path(path) is expected


def test_classify_conflicts_is_fail_closed():
    assert classify_conflicts(True).conflict_class is ConflictClass.NONE
    assert classify_conflicts(None).conflict_class is ConflictClass.UNSAFE
    assert classify_conflicts(False).reason == "CONFLICT_WITHOUT_PATH_EVIDENCE"
    assert classify_conflicts(True, ["a.py"]).reason == "MERGEABLE_WITH_CONFLICT_PATHS"
    mixed = classify_conflicts(False, ["README.md", "src/app.py"])
    assert mixed.conflict_class is ConflictClass.SEMANTIC
    assert mixed.next_gate is Gate.BUILDER_RESOLVE
    cross = classify_conflicts(False, ["README.md"], cross_repo=True)
    assert cross.conflict_class is ConflictClass.CROSS_REPO
    assert cross.next_gate is Gate.CONVERGENCE
    unsafe = classify_conflicts(False, [".github/workflows/ci.yml"], cross_repo=True)
    assert unsafe.conflict_class is ConflictClass.UNSAFE
    assert unsafe.next_gate is Gate.HUMAN_DECISION


def test_clean_exact_head_is_merge_ready_and_authorizes_merge(store):
    coordinator = prepare_merge(store, MISSION)
    decision = coordinator.evaluate(MISSION, clean_snapshot())
    assert decision.verdict is Verdict.MERGE_READY
    assert decision.merge_allowed is True
    assert decision.next_gate is Gate.NONE
    assert decision.reasons == ()
    assert (decision.reviewed_by, decision.verified_by) == ("reviewer-1", "verifier-1")
    assert store.get_mission(MISSION)["status"] == MissionStatus.MERGE_READY.value
    assert store.merge_authorization(MISSION)["authorized"] is True
    assert ApprovalPolicy(store).evaluate(MISSION, "merge").allowed is True


def test_new_push_makes_approvals_stale_and_revokes_merge_ready(store):
    coordinator = prepare_merge(store, MISSION)
    assert coordinator.evaluate(MISSION, clean_snapshot()).merge_allowed

    head = coordinator.record_head(MISSION, HEAD_B, "builder-1")
    assert head["changed"] is True
    assert head["stale_evidence_count"] == 2
    assert store.get_mission(MISSION)["status"] == MissionStatus.IN_REVIEW.value
    authorization = store.merge_authorization(MISSION)
    assert authorization["authorized"] is False
    assert "MERGE_DECISION_STALE_HEAD" in authorization["reasons"]
    assert ApprovalPolicy(store).evaluate(MISSION, "merge").allowed is False

    decision = coordinator.evaluate(MISSION, clean_snapshot(HEAD_B))
    assert decision.verdict is Verdict.BLOCKED
    assert {"REVIEWER_STALE", "VERIFIER_STALE", "CHECKPOINT_STALE"} <= set(decision.reasons)
    assert decision.next_gate is Gate.BUILDER_RESOLVE


def test_pr_head_must_match_recorded_mission_head(store):
    coordinator = prepare_merge(store, MISSION)
    decision = coordinator.evaluate(MISSION, clean_snapshot(HEAD_B))
    assert "PR_HEAD_MISMATCH" in decision.reasons
    assert decision.merge_allowed is False


def test_writer_cannot_supply_review_or_verification(store):
    coordinator = prepare_merge(store, MISSION)
    for role in EvidenceRole:
        with pytest.raises(EvidenceRejected, match="self-review"):
            coordinator.record_evidence(
                MISSION,
                role=role,
                head_sha=HEAD_A,
                actor="builder-1",
                verdict=EvidenceVerdict.ACCEPTED,
            )


def test_evidence_must_bind_to_current_head(store):
    coordinator = MergeCoordinator(store)
    with pytest.raises(EvidenceRejected, match="no recorded head"):
        coordinator.record_evidence(
            MISSION,
            role=EvidenceRole.REVIEWER,
            head_sha=HEAD_A,
            actor="reviewer-1",
            verdict=EvidenceVerdict.ACCEPTED,
        )
    coordinator.record_head(MISSION, HEAD_A, "builder-1")
    with pytest.raises(EvidenceRejected, match="does not match"):
        coordinator.record_evidence(
            MISSION,
            role=EvidenceRole.REVIEWER,
            head_sha=HEAD_B,
            actor="reviewer-1",
            verdict=EvidenceVerdict.ACCEPTED,
        )
    with pytest.raises(ValueError):
        coordinator.record_head(MISSION, "abc123", "builder-1")


def test_reviewer_and_verifier_must_be_distinct(store):
    coordinator = prepare_merge(store, MISSION)
    coordinator.record_evidence(
        MISSION,
        role=EvidenceRole.VERIFIER,
        head_sha=HEAD_A,
        actor="reviewer-1",
        verdict=EvidenceVerdict.ACCEPTED,
    )
    decision = coordinator.evaluate(MISSION, clean_snapshot())
    assert decision.reasons == ("VERIFIER_SAME_AS_REVIEWER",)
    assert decision.next_gate is Gate.VERIFY


def test_rejection_or_open_blockers_after_ready_supersede_authorization(store):
    coordinator = prepare_merge(store, MISSION)
    assert coordinator.evaluate(MISSION, clean_snapshot()).merge_allowed
    coordinator.record_evidence(
        MISSION,
        role=EvidenceRole.REVIEWER,
        head_sha=HEAD_A,
        actor="reviewer-2",
        verdict=EvidenceVerdict.ACCEPTED,
        blockers=["auth regression"],
    )
    assert store.merge_authorization(MISSION)["reasons"] == ["MERGE_DECISION_SUPERSEDED"]
    decision = coordinator.evaluate(MISSION, clean_snapshot())
    assert decision.reasons == ("REVIEWER_BLOCKERS_OPEN",)
    assert store.get_mission(MISSION)["status"] == MissionStatus.IN_REVIEW.value


@pytest.mark.parametrize(
    ("overrides", "reason", "gate"),
    [
        ({"target_sha": "d" * 40}, "BASE_STALE", Gate.BUILDER_RESOLVE),
        (
            {"mergeable": False, "conflicted_paths": ["src/app.py"]},
            "CONFLICT_SEMANTIC",
            Gate.BUILDER_RESOLVE,
        ),
        (
            {"mergeable": False, "conflicted_paths": ["README.md"], "cross_repo_conflict": True},
            "CONFLICT_CROSS_REPO",
            Gate.CONVERGENCE,
        ),
        (
            {"mergeable": False, "conflicted_paths": [".github/workflows/ci.yml"]},
            "CONFLICT_UNSAFE",
            Gate.HUMAN_DECISION,
        ),
        ({"mergeable": None}, "MERGEABILITY_UNKNOWN", Gate.HUMAN_DECISION),
        ({"checks": {"ci": "failure"}}, "CHECK_NOT_GREEN:ci=failure", Gate.CI),
        ({"checks": {}}, "CHECK_MISSING:ci", Gate.CI),
        ({"required_checks": []}, "REQUIRED_CHECKS_UNDECLARED", Gate.CI),
        ({"protected_rules_allow": None}, "PROTECTED_RULES_NOT_CONFIRMED", Gate.CI),
        ({"unresolved_review_threads": 2}, "REVIEW_THREADS_UNRESOLVED", Gate.REVIEW),
        ({"head_branch": "main"}, "HEAD_BRANCH_PROTECTED", Gate.HUMAN_DECISION),
        ({"base_sha": "not-a-sha"}, "INVALID_BASE_SHA", Gate.HUMAN_DECISION),
    ],
)
def test_each_merge_predicate_fails_closed(store, overrides, reason, gate):
    coordinator = prepare_merge(store, MISSION)
    decision = coordinator.evaluate(MISSION, clean_snapshot(**overrides))
    assert decision.verdict is Verdict.BLOCKED
    assert reason in decision.reasons
    assert decision.next_gate is gate
    assert store.merge_authorization(MISSION)["authorized"] is False


def test_mission_branch_binding(store):
    store.upsert_mission(Mission(MISSION, "repo", "goal", branch="impl/other"))
    coordinator = prepare_merge(store, MISSION)
    decision = coordinator.evaluate(MISSION, clean_snapshot())
    assert decision.reasons == ("PR_BRANCH_MISMATCH",)


def test_dirty_checkpoint_blocks(store):
    coordinator = prepare_merge(store, MISSION)
    store.record_checkpoint(
        MISSION, "builder-1", "WIP", head_sha=HEAD_A, dirty_count=3, tests={},
        blockers=[], next_task_requested=False,
    )
    assert coordinator.evaluate(MISSION, clean_snapshot()).reasons == ("CHECKPOINT_DIRTY",)


def test_dependency_order_is_enforced(store):
    store.upsert_mission(Mission("PAS-250", "repo", "upstream"))
    store.add_dependency(MISSION, "PAS-250")
    with pytest.raises(ValueError, match="cycle"):
        store.add_dependency("PAS-250", MISSION)
    with pytest.raises(ValueError):
        store.add_dependency(MISSION, MISSION)

    coordinator = prepare_merge(store, MISSION)
    decision = coordinator.evaluate(MISSION, clean_snapshot())
    assert decision.reasons == ("DEPENDENCY_PENDING:PAS-250=READY",)
    assert decision.next_gate is Gate.DEPENDENCIES

    store.upsert_mission(Mission("PAS-250", "repo", "upstream", status=MissionStatus.CERTIFIED))
    assert coordinator.evaluate(MISSION, clean_snapshot()).merge_allowed


def test_completion_and_merge_ready_transitions_are_gated(store):
    LeaseManager(store).claim(MISSION, "builder-1")
    for status in (MissionStatus.COMPLETE, MissionStatus.MERGE_READY):
        with pytest.raises(CompletionBlocked) as blocked:
            LeaseManager(store).release(MISSION, "builder-1", next_status=status)
        assert "NO_MERGE_DECISION" in blocked.value.reasons
        with pytest.raises(CompletionBlocked):
            store.set_status(MISSION, status)
    # Lease is untouched by a blocked release.
    assert LeaseManager(store).current(MISSION)["agent_id"] == "builder-1"

    coordinator = prepare_merge(store, MISSION, claim=False)
    assert coordinator.evaluate(MISSION, clean_snapshot()).merge_allowed
    LeaseManager(store).release(MISSION, "builder-1", next_status=MissionStatus.COMPLETE)
    assert store.get_mission(MISSION)["status"] == MissionStatus.COMPLETE.value


def test_decision_ledger_records_exact_snapshot(store):
    coordinator = prepare_merge(store, MISSION)
    coordinator.evaluate(MISSION, clean_snapshot(target_sha="e" * 40))
    latest = coordinator.latest_decision(MISSION)
    assert latest["verdict"] == "BLOCKED"
    assert latest["head_sha"] == HEAD_A
    assert latest["snapshot"]["base_sha"] == BASE
    assert latest["snapshot"]["target_sha"] == "e" * 40
    events = [row["event_type"] for row in store.events(MISSION)]
    assert "MERGE_DECISION_RECORDED" in events


def test_initialize_migrates_existing_database(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE missions (mission_id TEXT PRIMARY KEY, repository TEXT NOT NULL, "
        "goal TEXT NOT NULL, status TEXT NOT NULL, branch TEXT, worktree TEXT, base_sha TEXT, "
        "head_sha TEXT, required_approval INTEGER NOT NULL, acceptance_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO missions VALUES ('OLD-1','repo','goal','READY',NULL,NULL,NULL,NULL,1,'[]',"
        "'2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.close()
    store = MissionStore(path)
    store.initialize()
    store.initialize()
    assert store.merge_authorization("OLD-1")["reasons"] == [
        "NO_MISSION_HEAD",
        "NO_MERGE_DECISION",
    ]
