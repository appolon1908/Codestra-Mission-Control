from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ImplementationExecutionState(StrEnum):
    STARTED = "STARTED"
    IMPLEMENTED = "IMPLEMENTED"
    PROVEN = "PROVEN"
    NEEDS_REWORK = "NEEDS_REWORK"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ImplementationProofDecision:
    state: ImplementationExecutionState
    push_proven: bool
    eligible_for_review: bool
    reasons: tuple[str, ...]


def tests_passed(tests: dict[str, Any]) -> bool:
    if tests.get("passed") is True:
        return True
    status = str(tests.get("status", "")).strip().upper()
    if status in {"PASS", "PASSED", "GREEN", "SUCCESS"}:
        return True
    total = tests.get("total")
    failed = tests.get("failed")
    if isinstance(total, int) and total > 0 and failed == 0:
        return True
    return False


def evaluate_implementation_proof(
    *,
    local_commit_sha: str | None,
    pushed_branch_sha: str | None,
    pr_head_sha: str | None,
    pr_url: str | None,
    tests: dict[str, Any],
    implementation_files: list[str],
    api_required: bool,
    api_endpoints: list[str],
) -> ImplementationProofDecision:
    reasons: list[str] = []

    if not implementation_files:
        reasons.append("material implementation files are required")
    if not tests_passed(tests):
        reasons.append("passing test evidence is required")
    if api_required and not api_endpoints:
        reasons.append("API/endpoint evidence is required for this mission")
    if not local_commit_sha:
        reasons.append("local delivery commit SHA is required")
    if not pushed_branch_sha:
        reasons.append("pushed remote branch SHA is required")
    if not pr_head_sha:
        reasons.append("pull request head SHA is required")
    if not pr_url:
        reasons.append("pull request URL is required")

    shas = [local_commit_sha, pushed_branch_sha, pr_head_sha]
    present_shas = [sha for sha in shas if sha]
    push_proven = (
        len(present_shas) == 3
        and len(set(present_shas)) == 1
    )
    if len(present_shas) == 3 and not push_proven:
        reasons.append(
            "delivery SHA mismatch: local commit, pushed branch and PR head must match"
        )

    eligible_for_review = not reasons and push_proven
    return ImplementationProofDecision(
        state=(
            ImplementationExecutionState.PROVEN
            if eligible_for_review
            else ImplementationExecutionState.NEEDS_REWORK
        ),
        push_proven=push_proven,
        eligible_for_review=eligible_for_review,
        reasons=tuple(reasons),
    )
