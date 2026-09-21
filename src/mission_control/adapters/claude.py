from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .base import AgentAssignment
from .process import ProcessAgentAdapter


class ClaudeAdapter(ProcessAgentAdapter):
    name = "claude"

    SAFE_TOOLS = "Read,Edit,Write,Glob,Grep"

    def discover_executable(self) -> str:
        candidates: list[Path] = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm = Path(appdata) / "npm"
            package = npm / "node_modules" / "@anthropic-ai" / "claude-code"
            candidates.extend(
                [
                    package / "bin" / "claude.exe",
                    package
                    / "node_modules"
                    / "@anthropic-ai"
                    / "claude-code-win32-x64"
                    / "claude.exe",
                    npm / "claude.cmd",
                ]
            )
        candidates.extend(
            [
                Path(
                    r"C:\Users\agent\AppData\Roaming\npm\node_modules"
                    r"\@anthropic-ai\claude-code\bin\claude.exe"
                ),
                Path(r"C:\Users\agent\AppData\Roaming\npm\claude.cmd"),
            ]
        )
        return self._which_or_candidates("claude.exe", candidates)

    def auth_status(self) -> dict[str, object]:
        proc = subprocess.run(
            [self.executable, "auth", "status"],
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
        return {
            "authenticated": bool(payload.get("loggedIn")),
            "exit_code": proc.returncode,
            "auth_method": payload.get("authMethod"),
            "provider": payload.get("apiProvider"),
        }

    def build_command(
        self,
        assignment: AgentAssignment,
        prompt: str,
        execution_dir: Path,
    ) -> list[str]:
        del execution_dir
        return [
            self.executable,
            "--print",
            prompt,
            "--output-format",
            "stream-json",
            "--permission-mode",
            "auto",
            "--permission-prompts",
            "none",
            "--restricted",
            "--tools",
            self.SAFE_TOOLS,
            "--max-turns",
            str(assignment.max_turns),
            "--name",
            f"{assignment.mission_id}-{assignment.agent_id}",
        ]

    def extract_session_id(self, stdout_path: Path) -> str | None:
        if not stdout_path.is_file():
            return None
        for raw in stdout_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for key in ("session_id", "sessionId"):
                found = self._extract_json_key(event, key)
                if found:
                    return found
        return None
