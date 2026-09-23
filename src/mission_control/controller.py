
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from .lease import LeaseManager
from .models import MissionStatus
from .store import MissionStore


class ControllerAction(StrEnum):
    WAIT = "WAIT"
    CONTINUE = "CONTINUE"
    REASSIGN = "REASSIGN"
    REVIEW = "REVIEW"
    VERIFY = "VERIFY"
    MERGE_COORDINATE = "MERGE_COORDINATE"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class ControllerDecision:
    action: ControllerAction
    reason: str


class MissionController:
    def __init__(self, store: MissionStore) -> None:
        self.store = store
        self.leases = LeaseManager(store)

    def evaluate(self, mission_id: str) -> ControllerDecision:
        mission = self.store.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)

        status = MissionStatus(mission["status"])
        lease = self.leases.current(mission_id)

        if status == MissionStatus.COMPLETE:
            return ControllerDecision(ControllerAction.COMPLETE, "mission already complete")

        if status is MissionStatus.IN_REVIEW:
            return ControllerDecision(ControllerAction.REVIEW, "independent review required")

        if status is MissionStatus.VERIFYING:
            return ControllerDecision(ControllerAction.VERIFY, "independent verification required")

        if status in {MissionStatus.MERGE_COORDINATING, MissionStatus.MERGE_READY}:
            return ControllerDecision(
                ControllerAction.MERGE_COORDINATE,
                "merge coordinator must evaluate exact-SHA gates",
            )

        if lease:
            from datetime import datetime

            if datetime.fromisoformat(lease["expires_at"]) <= datetime.now(UTC):
                return ControllerDecision(
                    ControllerAction.REASSIGN,
                    f"writer lease expired for {lease['agent_id']}",
                )
            return ControllerDecision(
                ControllerAction.CONTINUE,
                f"active lease owned by {lease['agent_id']}",
            )

        if status in {MissionStatus.READY, MissionStatus.QUEUED, MissionStatus.WAITING}:
            return ControllerDecision(ControllerAction.REASSIGN, "no active writer lease")

        if status in {MissionStatus.BLOCKED, MissionStatus.NEEDS_DECISION}:
            return ControllerDecision(ControllerAction.WAIT, f"mission status is {status.value}")

        return ControllerDecision(ControllerAction.WAIT, f"no automatic action for {status.value}")
