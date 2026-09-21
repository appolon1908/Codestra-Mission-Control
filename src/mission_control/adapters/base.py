
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AgentAssignment:
    mission_id: str
    agent_id: str
    repository: str
    worktree: str
    branch: str
    base_sha: str
    goal: str
    acceptance: tuple[str, ...]


@dataclass(frozen=True)
class AgentHandoff:
    mission_id: str
    agent_id: str
    head_sha: str
    state: str
    summary: str
    tests: tuple[str, ...]
    blockers: tuple[str, ...]
    request_next_task: bool


class AgentAdapter(Protocol):
    name: str

    def dispatch(self, assignment: AgentAssignment) -> str:
        ...

    def stop(self, execution_id: str) -> None:
        ...

    def status(self, execution_id: str) -> str:
        ...
