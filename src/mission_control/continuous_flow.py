from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FlowAction(StrEnum):
    CONTINUE_IMPLEMENTATION = "CONTINUE_IMPLEMENTATION"
    AUTO_REMEDIATE = "AUTO_REMEDIATE"
    HANDOFF_REVIEW = "HANDOFF_REVIEW"
    HANDOFF_TESTING = "HANDOFF_TESTING"
    CERTIFICATION = "CERTIFICATION"
    NEXT_TASK = "NEXT_TASK"
    HUMAN_GATE = "HUMAN_GATE"


@dataclass(frozen=True)
class FlowContext:
    lane: str
    implementation_done: bool = False
    review_done: bool = False
    testing_done: bool = False
    certified: bool = False
    safe_remediations: tuple[str, ...] = ()
    production_effect: bool = False
    destructive_change: bool = False
    merge_authorized: bool = False


@dataclass(frozen=True)
class FlowDecision:
    action: FlowAction
    reasons: tuple[str, ...] = ()


SAFE_AUTO_REMEDIATIONS = frozenset(
    {
        "stale_base_sha",
        "wrong_header",
        "missing_test",
        "failing_gate",
        "format_failure",
        "lint_failure",
        "typecheck_failure",
    }
)


class ContinuousFlow:
    """Keeps lanes productive while preserving evidence and authority gates."""

    def decide(self, ctx: FlowContext) -> FlowDecision:
        if ctx.production_effect or ctx.destructive_change:
            return FlowDecision(FlowAction.HUMAN_GATE, ("privileged_or_destructive_boundary",))
        unsafe = tuple(r for r in ctx.safe_remediations if r not in SAFE_AUTO_REMEDIATIONS)
        if unsafe:
            return FlowDecision(FlowAction.HUMAN_GATE, unsafe)
        if ctx.safe_remediations:
            return FlowDecision(FlowAction.AUTO_REMEDIATE, ctx.safe_remediations)
        if ctx.lane == "IMPLEMENTATION":
            return FlowDecision(
                FlowAction.HANDOFF_REVIEW
                if ctx.implementation_done
                else FlowAction.CONTINUE_IMPLEMENTATION
            )
        if ctx.lane == "REVIEW":
            return FlowDecision(
                FlowAction.HANDOFF_TESTING
                if ctx.review_done
                else FlowAction.CONTINUE_IMPLEMENTATION
            )
        if ctx.lane == "TESTING":
            return FlowDecision(
                FlowAction.CERTIFICATION if ctx.testing_done else FlowAction.CONTINUE_IMPLEMENTATION
            )
        if ctx.certified:
            return FlowDecision(FlowAction.NEXT_TASK)
        return FlowDecision(FlowAction.CONTINUE_IMPLEMENTATION)


@dataclass(frozen=True)
class TestProfile:
    unit: bool = True
    integration: bool = True
    postman: bool = False
    postgres: bool = False
    api_contract: bool = False


def required_test_profile(*, has_api: bool, uses_postgres: bool) -> TestProfile:
    return TestProfile(postman=has_api, postgres=uses_postgres, api_contract=has_api)
