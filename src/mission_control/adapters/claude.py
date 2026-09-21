
from __future__ import annotations

from .base import AgentAssignment


class ClaudeAdapter:
    name = "claude"

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
            "Respect the writer lease. Never self-certify staging or production."
        )

    def dispatch(self, assignment: AgentAssignment) -> str:
        raise NotImplementedError("Claude worker integration is Phase 2")

    def stop(self, execution_id: str) -> None:
        raise NotImplementedError("Claude worker integration is Phase 2")

    def status(self, execution_id: str) -> str:
        raise NotImplementedError("Claude worker integration is Phase 2")
