from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from mission_control.git_executor import FileFence, GitWorktreeExecutor
from mission_control.lease import LeaseManager, LeaseNotOwned
from mission_control.store import MissionStore

from .base import AgentAssignment, AgentExecution


class AgentNotAuthenticated(RuntimeError):
    pass


class AgentProcessError(RuntimeError):
    pass


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        proc = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"PID eq {pid}",
                "/FO",
                "CSV",
                "/NH",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        output = proc.stdout.strip()
        return bool(output and not output.startswith("INFO:"))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ProcessAgentAdapter(ABC):
    name = "process"

    def __init__(
        self,
        store: MissionStore,
        *,
        runtime_root: str | Path,
        executable: str | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.store = store
        self.leases = LeaseManager(store)
        self.runtime_root = Path(runtime_root).resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.executable = executable or self.discover_executable()
        self.python = python_executable or sys.executable
        self.git = GitWorktreeExecutor()

    @abstractmethod
    def discover_executable(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def auth_status(self) -> dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def build_command(
        self,
        assignment: AgentAssignment,
        prompt: str,
        execution_dir: Path,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def extract_session_id(self, stdout_path: Path) -> str | None:
        raise NotImplementedError

    def _assert_writer_lease(self, assignment: AgentAssignment) -> None:
        lease = self.leases.current(assignment.mission_id)
        if not lease or lease["agent_id"] != assignment.agent_id:
            raise LeaseNotOwned(
                f"{assignment.agent_id} does not own writer lease for {assignment.mission_id}"
            )
        if lease["role"] != "WRITER":
            raise LeaseNotOwned(f"{assignment.agent_id} has {lease['role']} lease, not WRITER")

    def build_prompt(self, assignment: AgentAssignment) -> str:
        acceptance = "\n".join(f"- {item}" for item in assignment.acceptance)
        include = ", ".join(assignment.include_paths)
        exclude = ", ".join(assignment.exclude_paths) or "(none)"
        return (
            f"MISSION_ID={assignment.mission_id}\n"
            f"AGENT_ID={assignment.agent_id}\n"
            f"REPOSITORY={assignment.repository}\n"
            f"WORKTREE={assignment.worktree}\n"
            f"BRANCH={assignment.branch}\n"
            f"BASE_SHA={assignment.base_sha}\n"
            f"GOAL={assignment.goal}\n\n"
            f"FILE_FENCE_INCLUDE={include}\n"
            f"FILE_FENCE_EXCLUDE={exclude}\n\n"
            "ACCEPTANCE:\n"
            f"{acceptance}\n\n"
            "MANDATORY RULES:\n"
            "- Work only inside the assigned worktree and file fence.\n"
            "- Do not force-push, reset, clean, stash-destroy, merge, deploy, "
            "or mutate production.\n"
            "- Do not self-assign another mission.\n"
            "- Do not mark staging/production certified.\n"
            "- Stop after the bounded mission is implemented.\n"
            "- Leave a concise final handoff with changed files, tests run, "
            "blockers, and recommended next action.\n"
        )

    def dispatch(self, assignment: AgentAssignment) -> AgentExecution:
        self._assert_writer_lease(assignment)
        auth = self.auth_status()
        if not bool(auth.get("authenticated")):
            raise AgentNotAuthenticated(f"{self.name} is not authenticated")

        state = self.git.inspect(assignment.worktree)
        if state.head_sha != assignment.base_sha:
            raise AgentProcessError(
                f"assigned worktree head {state.head_sha} != exact base {assignment.base_sha}"
            )
        if state.branch != assignment.branch:
            raise AgentProcessError(
                f"assigned worktree branch {state.branch} != {assignment.branch}"
            )

        execution_id = str(uuid.uuid4())
        execution_dir = self.runtime_root / execution_id
        execution_dir.mkdir(parents=True, exist_ok=False)
        stdout_path = execution_dir / "stdout.log"
        stderr_path = execution_dir / "stderr.log"
        result_path = execution_dir / "result.json"
        spec_path = execution_dir / "spec.json"
        prompt_path = execution_dir / "prompt.txt"

        prompt = self.build_prompt(assignment)
        prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
        command = self.build_command(assignment, prompt, execution_dir)

        spec = {
            "command": command,
            "cwd": assignment.worktree,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "result_path": str(result_path),
        }
        spec_path.write_text(
            json.dumps(spec, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        runner = subprocess.Popen(
            [
                self.python,
                "-m",
                "mission_control.agent_process_runner",
                "--spec",
                str(spec_path),
            ],
            cwd=assignment.worktree,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy(),
            creationflags=creationflags,
            close_fds=True,
        )

        self.store.create_agent_execution(
            execution_id=execution_id,
            mission_id=assignment.mission_id,
            agent_id=assignment.agent_id,
            provider=self.name,
            state="RUNNING",
            runner_pid=runner.pid,
            worktree=assignment.worktree,
            command=command,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            result_path=str(result_path),
        )
        return self.status(execution_id)

    def status(self, execution_id: str) -> AgentExecution:
        row = self.store.get_agent_execution(execution_id)
        if not row:
            raise KeyError(execution_id)

        result_path = Path(row["result_path"])
        state = row["state"]
        exit_code = row["exit_code"]
        session_id = row["session_id"]

        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            exit_code = int(result["exit_code"])
            state = "COMPLETED" if exit_code == 0 else "FAILED"
            if not session_id:
                session_id = self.extract_session_id(Path(row["stdout_path"]))
            self.store.update_agent_execution(
                execution_id,
                state=state,
                session_id=session_id,
                exit_code=exit_code,
            )
        elif not _process_alive(int(row["runner_pid"])):
            state = "LOST"
            self.store.update_agent_execution(execution_id, state=state)

        return AgentExecution(
            execution_id=execution_id,
            mission_id=row["mission_id"],
            agent_id=row["agent_id"],
            provider=row["provider"],
            state=state,
            worktree=row["worktree"],
            runner_pid=int(row["runner_pid"]) if row["runner_pid"] else None,
            session_id=session_id,
            exit_code=exit_code,
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            result_path=row["result_path"],
        )

    def stop(self, execution_id: str) -> AgentExecution:
        row = self.store.get_agent_execution(execution_id)
        if not row:
            raise KeyError(execution_id)
        pid = int(row["runner_pid"])
        if _process_alive(pid):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass
        self.store.update_agent_execution(execution_id, state="STOPPED")
        return self.status(execution_id)

    def validate_file_fence(
        self,
        assignment: AgentAssignment,
    ) -> None:
        fence = FileFence(
            include=assignment.include_paths,
            exclude=assignment.exclude_paths,
        )
        self.git.validate_fence(assignment.worktree, fence)

    @staticmethod
    def _which_or_candidates(
        command: str,
        candidates: list[Path],
    ) -> str:
        names = [command]
        if os.name != "nt" and command.lower().endswith(".exe"):
            # Ubuntu/macOS installs expose the bare CLI name on PATH.
            names.insert(0, command[: -len(".exe")])
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        raise FileNotFoundError(command)

    @staticmethod
    def _extract_json_key(value: Any, key: str) -> str | None:
        if isinstance(value, dict):
            if key in value and isinstance(value[key], str):
                return value[key]
            for child in value.values():
                found = ProcessAgentAdapter._extract_json_key(child, key)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = ProcessAgentAdapter._extract_json_key(child, key)
                if found:
                    return found
        return None
