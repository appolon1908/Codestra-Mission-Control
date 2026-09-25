from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class WorkerReadinessSnapshot:
    host: str
    ready: bool
    checks: tuple[ReadinessCheck, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "ready": self.ready,
            "checks": [asdict(check) for check in self.checks],
        }


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )


def _discover(name: str, env_var: str | None = None) -> str | None:
    if env_var:
        configured = os.getenv(env_var)
        if configured and Path(configured).is_file():
            return configured

    found = shutil.which(name)
    if found:
        return found

    if os.name == "nt":
        candidates = [
            Path.home() / "AppData" / "Roaming" / "npm" / f"{name}.cmd",
            Path(r"C:\Users\Usuario\AppData\Roaming\npm") / f"{name}.cmd",
        ]
        program_files = Path(os.getenv("ProgramFiles", r"C:\Program Files"))
        if name == "tailscale":
            candidates.append(program_files / "Tailscale" / "tailscale.exe")
        elif name == "gh":
            candidates.append(program_files / "GitHub CLI" / "gh.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def _command_check(
    name: str,
    executable: str | None,
    args: list[str],
    authenticated_predicate: Callable[[subprocess.CompletedProcess[str]], bool],
    runner: CommandRunner,
) -> ReadinessCheck:
    if not executable:
        return ReadinessCheck(name, False, "executable_not_found")
    try:
        proc = runner([executable, *args])
    except (OSError, subprocess.SubprocessError) as exc:
        return ReadinessCheck(name, False, type(exc).__name__)

    output = (proc.stdout + "\n" + proc.stderr).strip()
    if authenticated_predicate(proc):
        return ReadinessCheck(name, True, "ready")
    return ReadinessCheck(name, False, output[:300] or f"exit_{proc.returncode}")


def _tailscale_check(runner: CommandRunner) -> ReadinessCheck:
    executable = _discover("tailscale", "TAILSCALE_BIN")
    if not executable:
        return ReadinessCheck("tailscale", False, "executable_not_found")
    try:
        proc = runner([executable, "status", "--json"])
    except (OSError, subprocess.SubprocessError) as exc:
        return ReadinessCheck("tailscale", False, type(exc).__name__)
    if proc.returncode != 0:
        return ReadinessCheck("tailscale", False, f"exit_{proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ReadinessCheck("tailscale", False, "invalid_status_json")
    state = str(payload.get("BackendState") or "")
    self_node = payload.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    ok = state == "Running" and bool(ips)
    detail = f"backend={state or 'unknown'} ips={len(ips)}"
    return ReadinessCheck("tailscale", ok, detail)


def _temporal_check(address: str | None = None) -> ReadinessCheck:
    target = address or os.getenv("TEMPORAL_ADDRESS")
    if not target:
        return ReadinessCheck("temporal", False, "TEMPORAL_ADDRESS_missing")
    host, sep, port_text = target.rpartition(":")
    if not sep or not host:
        return ReadinessCheck("temporal", False, "invalid_TEMPORAL_ADDRESS")
    try:
        port = int(port_text)
    except ValueError:
        return ReadinessCheck("temporal", False, "invalid_TEMPORAL_ADDRESS")
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except OSError as exc:
        return ReadinessCheck("temporal", False, type(exc).__name__)
    return ReadinessCheck("temporal", True, f"{host}:{port}")


def collect_worker_readiness(
    *,
    runner: CommandRunner = _run,
    temporal_address: str | None = None,
    host_name: str | None = None,
) -> WorkerReadinessSnapshot:
    gh = _command_check(
        "github",
        _discover("gh", "GH_BIN"),
        ["auth", "status"],
        lambda proc: proc.returncode == 0,
        runner,
    )
    codex = _command_check(
        "codex",
        _discover("codex", "CODEX_BIN"),
        ["login", "status"],
        lambda proc: proc.returncode == 0
        and "logged in" in (proc.stdout + proc.stderr).lower(),
        runner,
    )
    claude = _command_check(
        "claude",
        _discover("claude", "CLAUDE_BIN"),
        ["auth", "status"],
        lambda proc: proc.returncode == 0
        and (
            '"loggedIn":true' in (proc.stdout + proc.stderr).replace(" ", "")
            or '"loggedIn": true' in (proc.stdout + proc.stderr)
        ),
        runner,
    )
    tailscale = _tailscale_check(runner)
    temporal = _temporal_check(temporal_address)
    checks = (gh, codex, claude, tailscale, temporal)
    return WorkerReadinessSnapshot(
        host=host_name or socket.gethostname(),
        ready=all(check.ok for check in checks),
        checks=checks,
    )
