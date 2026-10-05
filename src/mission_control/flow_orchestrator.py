from __future__ import annotations

from dataclasses import dataclass

from .continuous_flow import ContinuousFlow, FlowAction, FlowContext
from .lane_taxonomy import AgentLane


@dataclass(frozen=True)
class Handoff:
    task_id: str
    from_lane: AgentLane
    to_lane: AgentLane | None
    release_current_agent: bool
    request_next_task: bool
    action: FlowAction


class FlowOrchestrator:
    """Turns lane completion into deterministic handoff plus immediate next work."""

    def __init__(self, flow: ContinuousFlow | None = None) -> None:
        self.flow = flow or ContinuousFlow()

    def on_checkpoint(
        self,
        *,
        task_id: str,
        lane: AgentLane,
        implementation_done: bool = False,
        review_done: bool = False,
        testing_done: bool = False,
        certified: bool = False,
        safe_remediations: tuple[str, ...] = (),
        production_effect: bool = False,
        destructive_change: bool = False,
    ) -> Handoff:
        decision = self.flow.decide(
            FlowContext(
                lane=lane.value,
                implementation_done=implementation_done,
                review_done=review_done,
                testing_done=testing_done,
                certified=certified,
                safe_remediations=safe_remediations,
                production_effect=production_effect,
                destructive_change=destructive_change,
            )
        )
        to_lane = None
        release = False
        request_next = False
        if decision.action is FlowAction.HANDOFF_REVIEW:
            to_lane, release, request_next = AgentLane.REVIEW, True, True
        elif decision.action is FlowAction.HANDOFF_TESTING:
            to_lane, release, request_next = AgentLane.TESTING, True, True
        elif decision.action is FlowAction.CERTIFICATION:
            release, request_next = True, True
        elif decision.action is FlowAction.NEXT_TASK:
            release, request_next = True, True
        return Handoff(task_id, lane, to_lane, release, request_next, decision.action)

    @staticmethod
    def next_ready_task(ranked_tasks: list, *, exclude_task_id: str) -> object | None:
        for ranked in ranked_tasks:
            task = getattr(ranked, "task", ranked)
            if getattr(task, "task_id", None) != exclude_task_id:
                return ranked
        return None
