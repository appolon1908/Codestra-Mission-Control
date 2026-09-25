from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mission_control.adapters.process import ProcessAgentAdapter
from mission_control.redaction import redact_text
from mission_control.store import MissionStore
from mission_control.worker_node import (
    NodeNotFound,
    NodeReadiness,
    NodeValidationError,
    WorkerNodeRegistry,
    parse_capabilities,
    probe_local_capabilities,
    sanitize_auth_status,
)
from mission_control.worker_node_api import WorkerNodeAPI

SRC = Path(__file__).resolve().parents[1] / "src"


def capabilities(**overrides) -> dict:
    body = {
        "hostname": "codestra-OptiPlex-3050",
        "os": "linux",
        "lanes": ["builder", "reviewer", "verifier"],
        "providers": ["claude", "codex"],
        "worktree_root": "/home/codestra/Worktrees",
        "max_parallel_writers": 3,
        "tools": {"git": "git version 2.51.0", "python": "3.14.4"},
        "worktree_root_writable": True,
        "disk_free_bytes": 10_000_000_000,
    }
    body.update(overrides)
    return body


@pytest.fixture()
def registry(tmp_path) -> WorkerNodeRegistry:
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    return WorkerNodeRegistry(store)


def ready_node(registry: WorkerNodeRegistry, node_id: str = "core-node-02") -> None:
    registry.register(node_id, capabilities())
    registry.heartbeat(node_id)
    registry.record_auth(node_id, "claude", {"authenticated": True, "auth_method": "oauth"})
    registry.record_auth(node_id, "codex", {"authenticated": True, "exit_code": 0})


def test_capabilities_are_durable_and_ready(registry, tmp_path):
    ready_node(registry)
    # A fresh registry over the same database proves durability.
    reopened = WorkerNodeRegistry(MissionStore(tmp_path / "mission.db"))
    snapshot = reopened.snapshot("core-node-02")
    assert snapshot["capabilities"]["lanes"] == ["builder", "reviewer", "verifier"]
    assert snapshot["capabilities"]["production_effects_enabled"] is False
    assert snapshot["readiness"]["state"] == NodeReadiness.READY
    assert snapshot["readiness"]["implementation_ready"] is True
    assert snapshot["readiness"]["ready_providers"] == ["claude", "codex"]
    events = [row["event_type"] for row in reopened.store.events("node:core-node-02")]
    assert events[0] == "WORKER_NODE_REGISTERED"
    assert events.count("WORKER_PROVIDER_AUTH_RECORDED") == 2


def test_review_only_lane_is_rejected():
    with pytest.raises(NodeValidationError) as exc:
        parse_capabilities("core-node-02", capabilities(lanes=["reviewer", "verifier"]))
    assert any("builder lane" in error for error in exc.value.errors)


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"lanes": ["builder", "janitor"]}, "unknown lane"),
        ({"providers": ["gemini"]}, "unknown provider"),
        ({"max_parallel_writers": 4}, "max_parallel_writers"),
        ({"max_parallel_writers": True}, "max_parallel_writers"),
        ({"worktree_root": ""}, "worktree_root is required"),
        ({"production_effects_enabled": "no"}, "production_effects_enabled"),
        ({"node_id": "other-node"}, "does not match path"),
    ],
)
def test_capability_validation(override, fragment):
    with pytest.raises(NodeValidationError) as exc:
        parse_capabilities("core-node-02", capabilities(**override))
    assert any(fragment in error for error in exc.value.errors)


def test_secret_fields_are_rejected_not_stored(registry):
    with pytest.raises(NodeValidationError):
        registry.register(
            "core-node-02",
            capabilities(tools={"git": "x"}, env={"ANTHROPIC_API_KEY": "sk-ant-abc"}),
        )
    assert registry.store.get_worker_node("core-node-02") is None
    registry.register("core-node-02", capabilities())
    with pytest.raises(NodeValidationError):
        registry.record_auth("core-node-02", "claude", {"authenticated": True, "token": "x"})
    assert registry.store.list_provider_auth("core-node-02") == []


def test_auth_status_is_redacted_and_allowlisted():
    status = sanitize_auth_status(
        "codex",
        {
            "authenticated": True,
            "exit_code": 0,
            "summary": "Logged in as ralph@example.com using key sk-proj-ABCDEF1234567890",
            "raw_env": {"OPENAI_API_KEY": "sk-proj-zzz"},
        },
    )
    payload = status.as_payload()
    assert set(payload) == {
        "provider",
        "authenticated",
        "auth_method",
        "exit_code",
        "detail",
        "checked_at",
    }
    assert "ralph@example.com" not in payload["detail"]
    assert "sk-proj" not in payload["detail"]
    assert "[REDACTED]" in payload["detail"]


def test_truthy_non_boolean_authenticated_is_not_trusted(registry):
    registry.register("core-node-02", capabilities())
    with pytest.raises(NodeValidationError):
        registry.record_auth("core-node-02", "claude", {"authenticated": "yes"})
    assert sanitize_auth_status("claude", {"authenticated": 1}).authenticated is False


def test_readiness_requires_heartbeat_auth_and_disabled_effects(registry):
    registry.register("core-node-02", capabilities())
    decision = registry.readiness("core-node-02")
    assert decision.state is NodeReadiness.NOT_READY
    assert "no heartbeat recorded" in decision.reasons
    assert "no authenticated implementation provider" in decision.reasons

    ready_node(registry)
    later = datetime.now(UTC) + timedelta(seconds=301)
    stale = registry.readiness("core-node-02", now=later)
    assert stale.state is NodeReadiness.NOT_READY
    assert any(reason.startswith("heartbeat stale") for reason in stale.reasons)

    registry.register("core-node-02", capabilities(production_effects_enabled=True))
    blocked = registry.readiness("core-node-02")
    assert "production effects must be disabled on worker nodes" in blocked.reasons

    registry.register("core-node-02", capabilities(tools={}, worktree_root_writable=False))
    broken = registry.readiness("core-node-02")
    assert "git is not available" in broken.reasons
    assert "worktree root is not writable" in broken.reasons


def test_single_unauthenticated_provider_degrades(registry):
    ready_node(registry)
    registry.record_auth("core-node-02", "codex", {"authenticated": False, "exit_code": 1})
    decision = registry.readiness("core-node-02")
    assert decision.state is NodeReadiness.DEGRADED
    assert decision.implementation_ready is True
    assert decision.ready_providers == ("claude",)
    assert "codex: not authenticated" in decision.warnings

    far = datetime.now(UTC) + timedelta(seconds=3601)
    registry.heartbeat("core-node-02")
    expired = registry.readiness("core-node-02", now=far)
    assert "no authenticated implementation provider" in expired.reasons


def test_probe_auth_survives_missing_cli_and_strips_secret_keys(registry):
    registry.register("core-node-02", capabilities())

    def missing() -> dict:
        raise FileNotFoundError("codex.exe")

    results = registry.probe_auth(
        "core-node-02",
        {
            "claude": lambda: {"authenticated": True, "access_token": "abc", "exit_code": 0},
            "codex": missing,
        },
    )
    by_provider = {item.provider: item for item in results}
    assert by_provider["claude"].authenticated is True
    assert by_provider["codex"].authenticated is False
    assert "FileNotFoundError" in (by_provider["codex"].detail or "")


def test_unknown_node_raises(registry):
    with pytest.raises(NodeNotFound):
        registry.heartbeat("missing")
    with pytest.raises(NodeNotFound):
        registry.record_auth("missing", "claude", {"authenticated": True})


def test_local_probe_produces_valid_registration(tmp_path):
    body = probe_local_capabilities(
        "core-node-02",
        lanes=["builder"],
        providers=["claude"],
        worktree_root=tmp_path / "worktrees",
    )
    caps = parse_capabilities("core-node-02", body)
    assert caps.worktree_root_writable is True
    assert caps.production_effects_enabled is False
    assert "python" in caps.tools
    assert not list((tmp_path / "worktrees").iterdir())


@pytest.mark.skipif(os.name == "nt", reason="bare CLI names are a POSIX lookup")
def test_executable_lookup_accepts_bare_posix_name(tmp_path, monkeypatch):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert ProcessAgentAdapter._which_or_candidates("claude.exe", []) == str(binary)


def test_redact_text_limits_output():
    assert len(redact_text("x" * 900, limit=100)) == 100
    assert redact_text("Authorization: Bearer abcdefghijkl") == "Authorization: [REDACTED]"


# ---------------------------------------------------------------- HTTP API


@pytest.fixture()
def api(tmp_path):
    store = MissionStore(tmp_path / "api.db")
    store.initialize()
    server = WorkerNodeAPI(store).server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_api_register_heartbeat_auth_readiness_readback(api):
    status, created = call(api, "PUT", "/platform/v1/worker-nodes/core-node-02", capabilities())
    assert status == 201
    assert created["readiness"]["state"] == "NOT_READY"

    status, _ = call(api, "PUT", "/platform/v1/worker-nodes/core-node-02", capabilities())
    assert status == 200

    status, beat = call(api, "POST", "/platform/v1/worker-nodes/core-node-02/heartbeat")
    assert status == 200
    assert beat["last_heartbeat_at"]

    status, auth = call(
        api,
        "PUT",
        "/platform/v1/worker-nodes/core-node-02/provider-auth/claude",
        {"authenticated": True, "auth_method": "oauth", "detail": "user a@b.io"},
    )
    assert status == 200
    assert auth["detail"] == "user [REDACTED]"

    status, readiness = call(api, "GET", "/platform/v1/worker-nodes/core-node-02/readiness")
    assert status == 200
    assert readiness["state"] == "DEGRADED"
    assert readiness["implementation_ready"] is True
    assert readiness["ready_providers"] == ["claude"]

    status, auth_list = call(api, "GET", "/platform/v1/worker-nodes/core-node-02/provider-auth")
    assert status == 200
    assert [item["provider"] for item in auth_list["items"]] == ["claude"]

    status, listing = call(api, "GET", "/platform/v1/worker-nodes")
    assert status == 200
    assert listing["count"] == 1
    assert listing["items"][0]["node_id"] == "core-node-02"

    status, single = call(api, "GET", "/platform/v1/worker-nodes/core-node-02")
    assert status == 200
    assert single["capabilities"]["hostname"] == "codestra-OptiPlex-3050"


def test_api_rejects_invalid_and_secret_payloads(api):
    status, error = call(
        api,
        "PUT",
        "/platform/v1/worker-nodes/core-node-02",
        capabilities(lanes=["reviewer"]),
    )
    assert status == 422
    assert error["error"] == "invalid_worker_node"

    status, error = call(
        api,
        "PUT",
        "/platform/v1/worker-nodes/core-node-02",
        capabilities(api_key="sk-ant-1234567890"),
    )
    assert status == 422
    assert "api_key" in error["errors"][0]

    call(api, "PUT", "/platform/v1/worker-nodes/core-node-02", capabilities())
    status, error = call(
        api,
        "PUT",
        "/platform/v1/worker-nodes/core-node-02/provider-auth/claude",
        {"authenticated": True, "oauth_token": "abc"},
    )
    assert status == 422

    status, error = call(
        api,
        "PUT",
        "/platform/v1/worker-nodes/core-node-02/provider-auth/gemini",
        {"authenticated": True},
    )
    assert status == 422


def test_api_not_found_and_bad_body(api):
    assert call(api, "GET", "/platform/v1/worker-nodes/missing")[0] == 404
    assert call(api, "GET", "/platform/v1/worker-nodes/missing/readiness")[0] == 404
    assert call(api, "GET", "/platform/v1/worker-nodes/missing/provider-auth")[0] == 404
    assert call(api, "POST", "/platform/v1/worker-nodes/missing/heartbeat")[0] == 404
    status, _ = call(
        api,
        "PUT",
        "/platform/v1/worker-nodes/missing/provider-auth/claude",
        {"authenticated": True},
    )
    assert status == 404
    assert call(api, "GET", "/platform/v1/nope")[0] == 404
    assert call(api, "GET", "/health") == (
        200,
        {"ok": True, "service": "mission-control-worker-node-api"},
    )
    request = urllib.request.Request(
        api + "/platform/v1/worker-nodes/core-node-02",
        data=b"not-json",
        method="PUT",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 400


# --------------------------------------------------------------------- CLI


def run_cli(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run(
        [sys.executable, "-m", "mission_control.cli", "--db", str(db), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_cli_node_probe_status_and_listing(tmp_path):
    db = tmp_path / "cli.db"
    probe = run_cli(
        db,
        "node-probe",
        "--node",
        "core-node-02",
        "--lane",
        "builder",
        "--lane",
        "verifier",
        "--provider",
        "claude",
        "--worktree-root",
        str(tmp_path / "worktrees"),
        "--skip-auth",
    )
    assert probe.returncode == 0, probe.stderr
    snapshot = json.loads(probe.stdout)
    assert snapshot["capabilities"]["lanes"] == ["builder", "verifier"]
    assert snapshot["last_heartbeat_at"]
    assert snapshot["readiness"]["reasons"] == ["no authenticated implementation provider"]

    listing = json.loads(run_cli(db, "nodes").stdout)
    assert listing["count"] == 1
    assert listing["implementation_ready"] == 0

    heartbeat = json.loads(run_cli(db, "node-heartbeat", "--node", "core-node-02").stdout)
    assert heartbeat["node_id"] == "core-node-02"

    missing = run_cli(db, "node-status", "--node", "nope")
    assert missing.returncode != 0
    assert "node_not_found" in missing.stderr

    rejected = run_cli(
        db,
        "node-register",
        "--node",
        "core-node-03",
        "--capabilities-json",
        json.dumps(capabilities(lanes=["reviewer"])),
    )
    assert rejected.returncode != 0
    assert "invalid_worker_node" in rejected.stderr
