from __future__ import annotations

import subprocess

from mission_control import worker_readiness
from mission_control.worker_readiness import ReadinessCheck, collect_worker_readiness


def test_worker_readiness_requires_all_gates(monkeypatch):
    monkeypatch.setattr(
        worker_readiness,
        "_discover",
        lambda name, env_var=None: name,
    )
    monkeypatch.setattr(
        worker_readiness,
        "_tailscale_check",
        lambda runner: ReadinessCheck("tailscale", True, "ready"),
    )
    monkeypatch.setattr(
        worker_readiness,
        "_temporal_check",
        lambda address=None: ReadinessCheck("temporal", True, "ready"),
    )

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, "github.com authenticated", "")
        if command[0] == "codex":
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        if command[0] == "claude":
            return subprocess.CompletedProcess(command, 0, '{"loggedIn": true}', "")
        raise AssertionError(command)

    snapshot = collect_worker_readiness(runner=runner, host_name="worker-1")

    assert snapshot.ready is True
    assert snapshot.host == "worker-1"
    assert all(check.ok for check in snapshot.checks)


def test_worker_readiness_fails_closed_on_codex_auth(monkeypatch):
    monkeypatch.setattr(
        worker_readiness,
        "_discover",
        lambda name, env_var=None: name,
    )
    monkeypatch.setattr(
        worker_readiness,
        "_tailscale_check",
        lambda runner: ReadinessCheck("tailscale", True, "ready"),
    )
    monkeypatch.setattr(
        worker_readiness,
        "_temporal_check",
        lambda address=None: ReadinessCheck("temporal", True, "ready"),
    )

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, "authenticated", "")
        if command[0] == "codex":
            return subprocess.CompletedProcess(command, 1, "Not logged in", "")
        if command[0] == "claude":
            return subprocess.CompletedProcess(command, 0, '{"loggedIn": true}', "")
        raise AssertionError(command)

    snapshot = collect_worker_readiness(runner=runner, host_name="worker-1")

    assert snapshot.ready is False
    by_name = {check.name: check for check in snapshot.checks}
    assert by_name["codex"].ok is False
    assert "Not logged in" in by_name["codex"].detail


def test_worker_readiness_serializes_without_secrets():
    snapshot = worker_readiness.WorkerReadinessSnapshot(
        host="worker",
        ready=False,
        checks=(ReadinessCheck("github", False, "not authenticated"),),
    )
    payload = snapshot.as_dict()

    assert payload == {
        "host": "worker",
        "ready": False,
        "checks": [{"name": "github", "ok": False, "detail": "not authenticated"}],
    }
