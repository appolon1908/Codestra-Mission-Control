
"""Temporal workflow skeleton.

Temporal is optional in Phase 0. Install the temporal extra to activate it.
All network and filesystem side effects remain activities.
"""

from __future__ import annotations

try:
    from datetime import timedelta
    from temporalio import workflow
except ImportError:
    workflow = None


if workflow is not None:

    @workflow.defn
    class MissionWorkflow:
        @workflow.run
        async def run(self, mission_id: str) -> str:
            while True:
                decision = await workflow.execute_activity(
                    "evaluate_mission",
                    mission_id,
                    start_to_close_timeout=timedelta(seconds=30),
                )
                action = decision["action"]

                if action == "COMPLETE":
                    return "COMPLETE"

                if action == "REASSIGN":
                    await workflow.execute_activity(
                        "dispatch_next_agent",
                        mission_id,
                        start_to_close_timeout=timedelta(minutes=2),
                    )
                elif action == "REVIEW":
                    await workflow.execute_activity(
                        "request_review",
                        mission_id,
                        start_to_close_timeout=timedelta(minutes=2),
                    )

                await workflow.sleep(timedelta(seconds=30))
