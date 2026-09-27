from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkstationPolicy:
    workstation: str
    role: str
    implementation_allowed: bool
    certification_allowed: bool
    publication_allowed: bool


class ImplementationPolicy:
    IMPLEMENTATION_AGENT_TYPES = frozenset({"claude", "codex", "api", "copilot", "grok"})
    SUPERVISOR_AGENT_TYPES = frozenset({"repo-supervisor", "workstation-supervisor", "ide-supervisor", "publication-supervisor"})

    def validate_assignment(self, *, agent_type: str, task_id: str | None,
                            lease_present: bool, branch: str | None,
                            worktree: str | None, review_only: bool = False) -> None:
        if agent_type in self.IMPLEMENTATION_AGENT_TYPES:
            if review_only:
                raise ValueError("implementation agent cannot receive review-only assignment")
            if not task_id or not lease_present or not branch or not worktree:
                raise ValueError("implementation agent requires atomic task, lease, branch and worktree")
            if branch in {"main", "master"}:
                raise ValueError("implementation agent cannot work on protected branch")
            return
        if agent_type in self.SUPERVISOR_AGENT_TYPES:
            return
        raise ValueError(f"unregistered agent type: {agent_type}")

    @staticmethod
    def workstation_defaults() -> tuple[WorkstationPolicy, ...]:
        return (
            WorkstationPolicy("UBUNTU_DESKTOP", "DEVELOPMENT_AND_CERTIFICATION", True, True, False),
            WorkstationPolicy("APPOLON_LAPTOP", "PUBLICATION_AUTHORITY", False, False, True),
            WorkstationPolicy("VS_CODE", "GOVERNED_IDE", True, False, False),
        )
