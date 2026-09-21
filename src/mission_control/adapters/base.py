from __future__ import annotations

from dataclasses import dataclass, field
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
    include_paths: tuple[str, ...] = ("**",)
    exclude_paths: tuple[str, ...] = ()
    max_turns: int = 30


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


@dataclass(frozen=True)
class AgentExecution:
    execution_id: str
    mission_id: str
    agent_id: str
    provider: str
    state: str
    worktree: str
    runner_pid: int | None = None
    session_id: str | None = None
    exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    result_path: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class AgentAdapter(Protocol):
    name: str

    def auth_status(self) -> dict[str, object]:
        ...

    def dispatch(self, assignment: AgentAssignment) -> AgentExecution:
        ...

    def stop(self, execution_id: str) -> AgentExecution:
        ...

    def status(self, execution_id: str) -> AgentExecution:
        ...
