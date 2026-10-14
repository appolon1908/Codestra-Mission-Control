"""Deterministic Temporal workflow definitions.

This module must remain free of filesystem, SQLite, subprocess, network and other
side-effect imports. Those belong in Temporal activities.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    from temporalio import workflow
    from temporalio.common import RetryPolicy
except ImportError:  # pragma: no cover - optional dependency
    workflow = None
    RetryPolicy = None


if workflow is not None:

    @workflow.defn(name="CodestraMissionWorkflow")
    class MissionWorkflow:
        def __init__(self) -> None:
            self._wake = False
            self._stop = False
            self._last_signal: dict[str, Any] | None = None
            self._last_decision: dict[str, str] | None = None
            self._iteration = 0

        @workflow.signal(name="checkpoint")
        async def checkpoint(self, payload: dict[str, Any]) -> None:
            self._last_signal = dict(payload)
            self._wake = True

        @workflow.signal(name="approval")
        async def approval(self, payload: dict[str, Any]) -> None:
            self._last_signal = dict(payload)
            self._wake = True

        @workflow.signal(name="stop")
        async def stop(self) -> None:
            self._stop = True
            self._wake = True

        @workflow.query(name="state")
        def state(self) -> dict[str, Any]:
            return {
                "iteration": self._iteration,
                "stopping": self._stop,
                "last_signal": self._last_signal,
                "last_decision": self._last_decision,
            }

        @workflow.run
        async def run(
            self,
            mission_id: str,
            poll_seconds: int = 30,
        ) -> str:
            retry = RetryPolicy(
                initial_interval=timedelta(seconds=1),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=5,
            )

            while not self._stop:
                self._iteration += 1
                decision = await workflow.execute_activity(
                    "evaluate_mission",
                    mission_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry,
                )
                self._last_decision = dict(decision)
                action = decision["action"]

                if action == "COMPLETE":
                    return "COMPLETE"

                if action == "REASSIGN":
                    await workflow.execute_activity(
                        "dispatch_next_agent",
                        mission_id,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry,
                    )
                elif action == "REVIEW":
                    await workflow.execute_activity(
                        "request_review",
                        mission_id,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry,
                    )
                elif action == "NEXT_IMPLEMENTATION":
                    await workflow.execute_activity(
                        "request_successor_implementation",
                        mission_id,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry,
                    )

                if self._last_signal:
                    signal_payload = dict(self._last_signal)
                    signal_payload["mission_id"] = mission_id
                    await workflow.execute_activity(
                        "workflow_signal",
                        signal_payload,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=retry,
                    )
                    self._last_signal = None

                self._wake = False
                try:
                    await workflow.wait_condition(
                        lambda: self._wake or self._stop,
                        timeout=timedelta(seconds=max(poll_seconds, 1)),
                    )
                except TimeoutError:
                    pass

            return "STOPPED"


else:

    class MissionWorkflow:  # pragma: no cover - placeholder without SDK
        """Placeholder so Phase-0 imports work without the optional Temporal SDK."""
