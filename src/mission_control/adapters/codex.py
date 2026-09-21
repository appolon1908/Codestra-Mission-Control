
from __future__ import annotations

from .base import AgentAssignment


class CodexAdapter:
    name = "codex"

    def build_prompt(self, assignment: AgentAssignment) -> str:
        acceptance = "\n".join(f"- {item}" for item in assignment.acceptance)
        return (
            f"MISSION_ID={assignment.mission_id}\n"
            f"REPOSITORY={assignment.repository}\n"
            f"WORKTREE={assignment.worktree}\n"
            f"BRANCH={assignment.branch}\n"
            f"BASE_SHA={assignment.base_sha}\n"
            f"GOAL={assignment.goal}\n"
            "ACCEPTANCE:\n"
            f"{acceptance}\n"
            "Do not start another mission. Emit a structured handoff when stopped."
        )

    def dispatch(self, assignment: AgentAssignment) -> str:
        raise NotImplementedError("Codex App Server/SDK integration is Phase 2")

    def stop(self, execution_id: str) -> None:
        raise NotImplementedError("Codex App Server/SDK integration is Phase 2")

    def status(self, execution_id: str) -> str:
        raise NotImplementedError("Codex App Server/SDK integration is Phase 2")
