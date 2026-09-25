from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from mission_control.redaction import redact_text

from .base import AgentAssignment
from .process import ProcessAgentAdapter


class CodexAdapter(ProcessAgentAdapter):
    name = "codex"

    def discover_executable(self) -> str:
        candidates: list[Path] = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            npm = Path(appdata) / "npm"
            package = npm / "node_modules" / "@openai" / "codex"
            candidates.extend(sorted(package.glob("**/bin/codex.exe")))
            candidates.append(npm / "codex.cmd")
        candidates.extend(
            [
                Path(
                    r"C:\Users\agent\AppData\Roaming\npm\node_modules\@openai"
                    r"\codex\node_modules\@openai\codex-win32-x64\vendor"
                    r"\x86_64-pc-windows-msvc\bin\codex.exe"
                ),
                Path(r"C:\Users\agent\AppData\Roaming\npm\codex.cmd"),
            ]
        )
        return self._which_or_candidates("codex.exe", candidates)

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
            "summary": redact_text(combined, limit=1000),
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
