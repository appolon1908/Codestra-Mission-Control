from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .base import AgentAssignment
from .process import ProcessAgentAdapter


class CodexAdapter(ProcessAgentAdapter):
    name = "codex"

    def discover_executable(self) -> str:
        candidates = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "codex.cmd")
        candidates.append(Path(r"C:\Users\agent\AppData\Roaming\npm\codex.cmd"))
        return self._which_or_candidates("codex", candidates)

    def auth_status(self) -> dict[str, object]:
        proc = subprocess.run(
            [self.executable, "login", "status"],
            text=True,
            capture_output=True,
            check=False,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        return {
            "authenticated": proc.returncode == 0 and "logged in" in combined.lower(),
            "exit_code": proc.returncode,
            "summary": combined[:1000],
        }

    def build_command(
        self,
        assignment: AgentAssignment,
        prompt: str,
        execution_dir: Path,
    ) -> list[str]:
        return [
            self.executable,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--approve-for-me",
            "--color",
            "never",
            "--cd",
            assignment.worktree,
            "--output-last-message",
            str(execution_dir / "final-message.txt"),
            prompt,
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
            if event.get("type") == "thread.started":
                value = event.get("thread_id")
                if isinstance(value, str):
                    return value
            found = self._extract_json_key(event, "thread_id")
            if found:
                return found
        return None
