
from __future__ import annotations

from dataclasses import dataclass

from .models import ApprovalLevel
from .store import MissionStore


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    required: ApprovalLevel
    approved: ApprovalLevel
    reason: str


ACTION_LEVELS = {
    "read": ApprovalLevel.READ_ONLY,
    "local_write": ApprovalLevel.LOCAL_WRITE,
    "branch_push": ApprovalLevel.BRANCH_PUSH,
    "merge": ApprovalLevel.MERGE,
    "staging_mutation": ApprovalLevel.STAGING_MUTATION,
    "production_effect": ApprovalLevel.PRODUCTION_EFFECT,
}


class ApprovalPolicy:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def evaluate(self, mission_id: str, action: str) -> PolicyDecision:
        if action not in ACTION_LEVELS:
            raise KeyError(action)
        required = ACTION_LEVELS[action]
        approved = self.store.highest_approval(mission_id)

        if required <= ApprovalLevel.LOCAL_WRITE:
            return PolicyDecision(True, required, approved, "low-risk action")

        if approved >= required >= ApprovalLevel.MERGE:
            authorization = self.store.merge_authorization(mission_id)
            if not authorization["authorized"]:
                return PolicyDecision(
                    False,
                    required,
                    approved,
                    f"{action} requires current exact-head merge-coordinator "
                    f"authorization: {', '.join(authorization['reasons'])}",
                )

        if approved >= required:
            return PolicyDecision(True, required, approved, "required approval recorded")

        return PolicyDecision(
            False,
            required,
            approved,
            f"{action} requires approval level {int(required)}; "
            f"highest recorded is {int(approved)}",
        )
