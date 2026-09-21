from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .lease import LeaseManager
from .models import ApprovalLevel
from .policy import ACTION_LEVELS, ApprovalPolicy
from .store import MissionStore


class ActorKind(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"


class ApprovalDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalContext:
    actor: str
    actor_kind: ActorKind
    ci_green: bool = False
    certification_passed: bool = False
    explicit_production_confirmation: bool = False


@dataclass(frozen=True)
class ApprovalResult:
    mission_id: str
    level: ApprovalLevel
    actor: str
    recorded: bool


class ApprovalEngine:
    def __init__(self, store: MissionStore) -> None:
        self.store = store
        self.leases = LeaseManager(store)
        self.policy = ApprovalPolicy(store)

    def approve(
        self,
        mission_id: str,
        level: ApprovalLevel,
        context: ApprovalContext,
    ) -> ApprovalResult:
        current = self.leases.current(mission_id)
        writer = current["agent_id"] if current else None

        if level >= ApprovalLevel.MERGE and writer and context.actor == writer:
            raise ApprovalDenied("independent reviewer required; writer cannot self-approve")

        if level >= ApprovalLevel.MERGE and not context.ci_green:
            raise ApprovalDenied("merge-or-higher approval requires green CI")

        if level >= ApprovalLevel.STAGING_MUTATION and not context.certification_passed:
            raise ApprovalDenied("staging-or-higher approval requires certification evidence")

        if level >= ApprovalLevel.PRODUCTION_EFFECT:
            if context.actor_kind is not ActorKind.HUMAN:
                raise ApprovalDenied("production effects require a human approver")
            if not context.explicit_production_confirmation:
                raise ApprovalDenied("production effect requires explicit confirmation")

        self.store.record_approval(mission_id, level, context.actor)
        return ApprovalResult(mission_id, level, context.actor, True)

    def can_execute(self, mission_id: str, action: str) -> bool:
        if action not in ACTION_LEVELS:
            raise KeyError(action)
        return self.policy.evaluate(mission_id, action).allowed
