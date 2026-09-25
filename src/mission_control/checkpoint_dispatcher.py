from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import MissionStatus
from .store import MissionStore


class CheckpointAction(StrEnum):
    BLOCKED = "BLOCKED"
    REWORK = "REWORK"
    CONTINUE = "CONTINUE"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class CheckpointDecision:
    mission_id: str
    action: CheckpointAction
    status: MissionStatus
    reason: str
    next_action: str
    head_sha: str | None


_FAILURE_WORDS = {"fail", "failed", "failure", "error", "errored", "red"}
_SUCCESS_KEYS = {"passed", "success", "successful", "green"}


def _has_test_failure(value: Any, *, key: str | None = None) -> bool:
    if isinstance(value, dict):
        return any(_has_test_failure(v, key=str(k).lower()) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_test_failure(v, key=key) for v in value)
    if isinstance(value, str):
        return value.strip().lower() in _FAILURE_WORDS
    if isinstance(value, bool) and key in _SUCCESS_KEYS:
        return not value
    return False


class CheckpointDispatcher:
    """Turn durable checkpoints into deterministic Mission Control handoffs."""

    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def evaluate(self, mission_id: str) -> CheckpointDecision:
        mission = self.store.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        checkpoint = self.store.latest_checkpoint(mission_id)
        if not checkpoint:
            raise RuntimeError(f"{mission_id} has no checkpoint")

        blockers = list(json.loads(checkpoint["blockers_json"] or "[]"))
        tests = json.loads(checkpoint["tests_json"] or "{}")
        dirty_count = int(checkpoint["dirty_count"] or 0)
        head_sha = checkpoint["head_sha"]
        requested = bool(checkpoint["next_task_requested"])

        if blockers:
            return CheckpointDecision(
                mission_id,
                CheckpointAction.BLOCKED,
                MissionStatus.BLOCKED,
                "checkpoint contains unresolved blockers",
                f"Resolve blocker: {blockers[0]}",
                head_sha,
            )

        if dirty_count:
            return CheckpointDecision(
                mission_id,
                CheckpointAction.REWORK,
                MissionStatus.WORKING,
                f"checkpoint has {dirty_count} dirty file(s)",
                "Preserve the work, make a coherent commit, rerun tests, then checkpoint again.",
                head_sha,
            )

        if _has_test_failure(tests):
            return CheckpointDecision(
                mission_id,
                CheckpointAction.REWORK,
                MissionStatus.WORKING,
                "checkpoint contains failing test evidence",
                "Fix the failing test/code path and checkpoint the exact tested HEAD again.",
                head_sha,
            )

        if requested and not tests:
            return CheckpointDecision(
                mission_id,
                CheckpointAction.REWORK,
                MissionStatus.WORKING,
                "next task requested without test evidence",
                "Run the mission-required tests and attach deterministic results before handoff.",
                head_sha,
            )

        if requested:
            if not head_sha:
                return CheckpointDecision(
                    mission_id,
                    CheckpointAction.REWORK,
                    MissionStatus.WORKING,
                    "next task requested without an exact HEAD SHA",
                    "Record the exact commit SHA and repeat the checkpoint.",
                    head_sha,
                )
            return CheckpointDecision(
                mission_id,
                CheckpointAction.REVIEW,
                MissionStatus.IN_REVIEW,
                "clean tested checkpoint requested handoff",
                f"Independently review and verify exact HEAD {head_sha}; do not self-certify.",
                head_sha,
            )

        return CheckpointDecision(
            mission_id,
            CheckpointAction.CONTINUE,
            MissionStatus.WORKING,
            "checkpoint does not request handoff",
            "Continue the bounded mission and emit a new checkpoint after meaningful progress.",
            head_sha,
        )

    def _next_task_path(self, mission_id: str) -> Path | None:
        mission = self.store.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        repository = self.store.get_repository(mission["repository"])
        if not repository or not repository["mission_channel_path"]:
            return None
        return Path(repository["mission_channel_path"]) / "NEXT_TASK.md"

    def process_latest(self, mission_id: str) -> CheckpointDecision:
        decision = self.evaluate(mission_id)
        mission = self.store.get_mission(mission_id)
        assert mission is not None
        if str(mission["status"]) != decision.status.value:
            self.store.set_status(mission_id, decision.status)

        path = self._next_task_path(mission_id)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                f"# Mission {mission_id} — {decision.action.value}\n\n"
                f"- Status: {decision.status.value}\n"
                f"- Exact HEAD: {decision.head_sha or 'none'}\n"
                f"- Reason: {decision.reason}\n\n"
                "## Next action\n\n"
                f"{decision.next_action}\n"
            )
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(content, encoding="utf-8", newline="\n")
            temp.replace(path)
        return decision
