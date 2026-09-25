from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NextTask:
    mission_id: str
    repository: str
    goal: str
    state: str
    head_sha: str | None
    blockers: tuple[str, ...]
    acceptance: tuple[str, ...]


def compile_next_task(mission: dict, checkpoint: dict) -> NextTask:
    blockers = tuple(json.loads(checkpoint.get("blockers_json") or "[]"))
    acceptance = tuple(json.loads(mission.get("acceptance_json") or "[]"))
    return NextTask(
        mission_id=str(mission["mission_id"]),
        repository=str(mission["repository"]),
        goal=str(mission["goal"]),
        state=str(checkpoint["state"]),
        head_sha=checkpoint.get("head_sha") or mission.get("head_sha"),
        blockers=blockers,
        acceptance=acceptance,
    )


def render_next_task(task: NextTask) -> str:
    if task.blockers:
        action = f"Resolve blocker: {task.blockers[0]}"
    elif task.acceptance:
        action = f"Advance the next unmet acceptance criterion: {task.acceptance[0]}"
    else:
        action = f"Continue the smallest verifiable implementation step toward: {task.goal}"

    acceptance_lines = "\n".join(f"- {item}" for item in task.acceptance) or "- No explicit criteria recorded"
    blocker_lines = "\n".join(f"- {item}" for item in task.blockers) or "- None"

    return (
        f"# NEXT TASK — {task.mission_id}\n\n"
        f"Repository: {task.repository}\n"
        f"Checkpoint state: {task.state}\n"
        f"Exact HEAD: {task.head_sha or 'UNKNOWN'}\n\n"
        f"## Goal\n{task.goal}\n\n"
        f"## Immediate action\n{action}\n\n"
        f"## Acceptance criteria\n{acceptance_lines}\n\n"
        f"## Current blockers\n{blocker_lines}\n\n"
        "## Safety\n"
        "- Preserve exact branch/HEAD authority.\n"
        "- Do not bypass review, CI, policy, staging, or production gates.\n"
        "- Checkpoint tests, blockers, exact HEAD, and the next action before handoff.\n"
    )


def write_next_task(worktree: str | Path, task: NextTask) -> Path:
    root = Path(worktree).resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"mission worktree is unavailable: {root}")
    target = root / "NEXT_TASK.md"
    temp = root / ".NEXT_TASK.md.tmp"
    temp.write_text(render_next_task(task), encoding="utf-8")
    temp.replace(target)
    return target
