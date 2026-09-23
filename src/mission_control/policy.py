
from __future__ import annotations

from dataclasses import dataclass

from .models import ApprovalGate, ApprovalLevel
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

        if required >= ApprovalLevel.MERGE:
            mission = self.store.get_mission(mission_id)
            head_sha = mission["head_sha"] if mission else None
            merge_authorization = (
                self.store.latest_valid_sha_approval(
                    mission_id,
                    ApprovalGate.MERGE_AUTHORIZATION,
                    head_sha,
                )
                if head_sha
                else None
            )
            if not merge_authorization:
                return PolicyDecision(
                    False,
                    required,
                    approved,
                    "merge-or-higher action requires exact-SHA Merge Coordinator authorization",
                )
            if required == ApprovalLevel.MERGE:
                return PolicyDecision(
                    True,
                    required,
                    ApprovalLevel.MERGE,
                    f"exact-SHA merge authorization recorded for {head_sha}",
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
