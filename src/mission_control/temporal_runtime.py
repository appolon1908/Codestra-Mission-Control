from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .controller import MissionController
from .store import MissionStore

try:
    from temporalio import activity
    from temporalio.client import Client
    from temporalio.worker import Worker
except ImportError:  # pragma: no cover - optional dependency
    activity = None
    Client = None
    Worker = None

from .temporal_workflow import MissionWorkflow

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


async def run_worker(config: TemporalRuntimeConfig) -> None:
    if Client is None or Worker is None:
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
