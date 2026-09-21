from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .controller import MissionController
from .store import MissionStore

try:
    from temporalio import activity, workflow
    from temporalio.client import Client
    from temporalio.common import RetryPolicy
    from temporalio.worker import Worker
except ImportError:  # pragma: no cover - optional dependency
    activity = None
    workflow = None
    Client = None
    RetryPolicy = None
    Worker = None


DEFAULT_TASK_QUEUE = "codestra-mission-control"
DEFAULT_NAMESPACE = "codestra-mission-control"


@dataclass(frozen=True)
class TemporalRuntimeConfig:
    address: str = "127.0.0.1:7233"
    namespace: str = DEFAULT_NAMESPACE
    task_queue: str = DEFAULT_TASK_QUEUE
    database: str = ".runtime/mission-control.db"
    poll_seconds: int = 30

    @classmethod
    def from_env(cls) -> TemporalRuntimeConfig:
        return cls(
            address=os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
            namespace=os.environ.get("TEMPORAL_NAMESPACE", DEFAULT_NAMESPACE),
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE),
            database=os.environ.get(
                "MISSION_CONTROL_DB",
                ".runtime/mission-control.db",
            ),
            poll_seconds=int(os.environ.get("MISSION_CONTROL_POLL_SECONDS", "30")),
        )


class MissionActivities:
    def __init__(self, database: str | Path) -> None:
        self.store = MissionStore(database)
        self.store.initialize()

    def _event(
        self,
        mission_id: str,
        event_type: str,
        *,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.store._event(
                conn,
                mission_id,
                event_type,
                agent_id,
                payload or {},
            )
            conn.execute("COMMIT")

    async def evaluate_mission(self, mission_id: str) -> dict[str, str]:
        decision = MissionController(self.store).evaluate(mission_id)
        result = {
            "action": decision.action.value,
            "reason": decision.reason,
        }
        self._event(mission_id, "TEMPORAL_EVALUATED", payload=result)
        return result

    async def dispatch_next_agent(self, mission_id: str) -> dict[str, str]:
        self._event(
            mission_id,
            "DISPATCH_REQUESTED",
            payload={"source": "temporal"},
        )
        return {
            "mission_id": mission_id,
            "status": "DISPATCH_REQUESTED",
        }

    async def request_review(self, mission_id: str) -> dict[str, str]:
        self._event(
            mission_id,
            "REVIEW_REQUESTED",
            payload={"source": "temporal"},
        )
        return {
            "mission_id": mission_id,
            "status": "REVIEW_REQUESTED",
        }

    async def workflow_signal(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        mission_id = str(payload["mission_id"])
        self._event(
            mission_id,
            "WORKFLOW_SIGNAL",
            agent_id=payload.get("agent_id"),
            payload=payload,
        )
        return {"recorded": True, "mission_id": mission_id}


if activity is not None:
    MissionActivities.evaluate_mission = activity.defn(
        name="evaluate_mission"
    )(MissionActivities.evaluate_mission)
    MissionActivities.dispatch_next_agent = activity.defn(
        name="dispatch_next_agent"
    )(MissionActivities.dispatch_next_agent)
    MissionActivities.request_review = activity.defn(
        name="request_review"
    )(MissionActivities.request_review)
    MissionActivities.workflow_signal = activity.defn(
        name="workflow_signal"
    )(MissionActivities.workflow_signal)


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


async def run_worker(config: TemporalRuntimeConfig) -> None:
    if Client is None or Worker is None or workflow is None:
        raise RuntimeError(
            'Temporal SDK is not installed; install with: pip install -e ".[temporal]"'
        )
    client = await Client.connect(
        config.address,
        namespace=config.namespace,
    )
    activities = MissionActivities(config.database)
    worker = Worker(
        client,
        task_queue=config.task_queue,
        workflows=[MissionWorkflow],
        activities=[
            activities.evaluate_mission,
            activities.dispatch_next_agent,
            activities.request_review,
            activities.workflow_signal,
        ],
    )
    await worker.run()


async def start_mission_workflow(
    config: TemporalRuntimeConfig,
    mission_id: str,
) -> dict[str, str]:
    if Client is None:
        raise RuntimeError(
            'Temporal SDK is not installed; install with: pip install -e ".[temporal]"'
        )
    client = await Client.connect(
        config.address,
        namespace=config.namespace,
    )
    workflow_id = f"mission/{mission_id}"
    handle = await client.start_workflow(
        "CodestraMissionWorkflow",
        args=[mission_id, config.poll_seconds],
        id=workflow_id,
        task_queue=config.task_queue,
    )
    return {
        "workflow_id": workflow_id,
        "run_id": handle.first_execution_run_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker")
    start = sub.add_parser("start")
    start.add_argument("--mission", required=True)

    args = parser.parse_args()
    config = TemporalRuntimeConfig.from_env()

    if args.command == "worker":
        asyncio.run(run_worker(config))
    elif args.command == "start":
        result = asyncio.run(start_mission_workflow(config, args.mission))
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
