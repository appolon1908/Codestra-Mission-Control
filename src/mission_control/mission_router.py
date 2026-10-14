from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class AtomicTask:
    task_id: str
    repository: str
    area: str
    sub_area: str
    mission_id: str
    priority: int = 50
    completion_percent: float = 0.0
    certified: bool = False
    dependencies: tuple[str, ...] = ()
    required_skills: frozenset[str] = frozenset()
    collision_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AgentCapacity:
    agent_id: str
    skills: frozenset[str] = frozenset()
    active_tasks: int = 0
    wip_limit: int = 1


@dataclass(frozen=True)
class RankedTask:
    task: AtomicTask
    score: float
    reasons: tuple[str, ...]


class MissionRouter:
    def __init__(self, *, target_floor: float = 60.0) -> None:
        if not 0 <= target_floor <= 100:
            raise ValueError("target_floor must be between 0 and 100")
        self.target_floor = target_floor

    def rank(
        self,
        tasks: Iterable[AtomicTask],
        agent: AgentCapacity,
        *,
        completed: set[str] | None = None,
        active_collision_keys: set[str] | None = None,
    ) -> list[RankedTask]:
        completed = completed or set()
        active_collision_keys = active_collision_keys or set()
        if agent.active_tasks >= agent.wip_limit:
            return []

        ranked: list[RankedTask] = []
        for task in tasks:
            if task.certified or task.completion_percent >= 100:
                continue
            if any(dep not in completed for dep in task.dependencies):
                continue
            if task.collision_keys & active_collision_keys:
                continue
            if not task.required_skills.issubset(agent.skills):
                continue

            dependency_unlock = len(task.dependencies) * 8.0
            below_floor = max(0.0, self.target_floor - task.completion_percent) * 0.6
            unfinished = max(0.0, 100.0 - task.completion_percent) * 0.15
            priority = max(0.0, 100.0 - float(task.priority)) * 0.4
            skill_bonus = len(task.required_skills & agent.skills) * 5.0
            score = dependency_unlock + below_floor + unfinished + priority + skill_bonus
            ranked.append(
                RankedTask(
                    task=task,
                    score=round(score, 2),
                    reasons=(
                        f"completion={task.completion_percent:.1f}%",
                        f"priority={task.priority}",
                        f"dependencies={len(task.dependencies)}",
                        f"skills={len(task.required_skills)}",
                    ),
                )
            )

        return sorted(ranked, key=lambda item: (-item.score, item.task.task_id))

    @staticmethod
    def progress(tasks: Iterable[AtomicTask]) -> dict[str, float]:
        rows = list(tasks)
        if not rows:
            return {"work_in_progress": 0.0, "certified": 0.0}
        wip = sum(min(100.0, max(0.0, task.completion_percent)) for task in rows) / len(rows)
        certified = sum(100.0 if task.certified else 0.0 for task in rows) / len(rows)
        return {"work_in_progress": round(wip, 2), "certified": round(certified, 2)}
