from mission_control.implementation_contract import (
    ImplementationExecutionState,
    evaluate_implementation_proof,
)
from mission_control.implementation_contract import (
    tests_passed as implementation_tests_passed,
)


def test_tests_passed_accepts_machine_readable_success():
    assert implementation_tests_passed({"passed": True})
    assert implementation_tests_passed({"status": "PASS"})
    assert implementation_tests_passed({"total": 54, "failed": 0})
    assert not implementation_tests_passed({"status": "FAILED"})
    assert not implementation_tests_passed({})


def test_proof_requires_material_implementation_and_tests():
    decision = evaluate_implementation_proof(
        local_commit_sha="abc",
        pushed_branch_sha="abc",
        pr_head_sha="abc",
        pr_url="https://github.com/example/repo/pull/1",
        tests={},
        implementation_files=[],
        api_required=False,
        api_endpoints=[],
    )
    assert decision.state == ImplementationExecutionState.NEEDS_REWORK
    assert not decision.eligible_for_review
    assert "material implementation files are required" in decision.reasons
    assert "passing test evidence is required" in decision.reasons


def test_proof_requires_api_evidence_when_api_is_in_scope():
    decision = evaluate_implementation_proof(
        local_commit_sha="abc",
        pushed_branch_sha="abc",
        pr_head_sha="abc",
        pr_url="https://github.com/example/repo/pull/1",
        tests={"passed": True},
        implementation_files=["src/api.py"],
        api_required=True,
        api_endpoints=[],
    )
    assert not decision.eligible_for_review
    assert "API/endpoint evidence is required for this mission" in decision.reasons


def test_proof_rejects_sha_mismatch():
    decision = evaluate_implementation_proof(
        local_commit_sha="abc",
        pushed_branch_sha="def",
        pr_head_sha="def",
        pr_url="https://github.com/example/repo/pull/1",
        tests={"status": "PASS"},
        implementation_files=["src/worker.py"],
        api_required=False,
        api_endpoints=[],
    )
    assert not decision.push_proven
    assert not decision.eligible_for_review
    assert any("SHA mismatch" in reason for reason in decision.reasons)


def test_proof_is_review_eligible_only_when_delivery_matches():
    decision = evaluate_implementation_proof(
        local_commit_sha="abc",
        pushed_branch_sha="abc",
        pr_head_sha="abc",
        pr_url="https://github.com/example/repo/pull/1",
        tests={"total": 12, "failed": 0},
        implementation_files=["src/worker.py", "tests/test_worker.py"],
        api_required=True,
        api_endpoints=["POST /platform/v1/agent-executions"],
    )
    assert decision.state == ImplementationExecutionState.PROVEN
    assert decision.push_proven
    assert decision.eligible_for_review
    assert decision.reasons == ()
