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

from mission_control.fabric_api import FabricAPI, FabricRuntime
from mission_control.network_fabric import (
    FabricConfigError,
    StatusSnapshot,
    load_inventory,
    load_policy_file,
    read_status_command,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 9, 25, 16, 0, tzinfo=UTC)

INVENTORY = load_inventory(
    {
        "tailnet": "t.ts.net",
        "nodes": [
            {
                "hostname": "mc",
                "dns": "mc.t.ts.net.",
                "ipv4": "100.76.208.87",
                "roles": ["mission-control"],
            },
            {
                "hostname": "worker",
                "dns": "worker.t.ts.net.",
                "ipv4": "100.64.209.77",
                "roles": ["workstation", "agent-worker"],
            },
        ],
    }
)


def status(worker_online=True, captured_at=NOW):
    return StatusSnapshot(
        source="test",
        captured_at=captured_at,
        payload={
            "BackendState": "Running",
            "Self": {
                "HostName": "mc",
                "DNSName": "mc.t.ts.net.",
                "TailscaleIPs": ["100.76.208.87"],
                "Online": True,
                "Tags": ["tag:mission-control"],
            },
            "Peer": {
                "w": {
                    "HostName": "worker",
                    "DNSName": "worker.t.ts.net.",
                    "TailscaleIPs": ["100.64.209.77"],
                    "Online": worker_online,
                    "LastSeen": "2026-09-25T15:55:00Z",
                    "Tags": ["tag:agent-worker"],
                }
            },
        },
    )


def serve(reader, policy=None):
    runtime = FabricRuntime(
        INVENTORY,
        policy or load_policy_file(CONFIG / "tailscale.policy-plan.json"),
        reader,
        clock=lambda: NOW,
        cache_seconds=0,
    )
    server = FabricAPI(runtime).server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def request(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_liveness_and_healthy_fabric_readback():
    server, base = serve(status)
    try:
        assert request(base + "/health") == (
            200,
            {"ok": True, "service": "mission-control-fabric-api"},
        )
        code, body = request(base + "/platform/v1/fabric/health")
        assert code == 200
        assert body["state"] == "healthy"
        assert body["counts"] == {"online": 2, "unexpected": 0}

        code, body = request(base + "/platform/v1/fabric/nodes")
        assert code == 200
        assert body["count"] == 2
        assert [node["hostname"] for node in body["nodes"]] == ["mc", "worker"]
        assert body["nodes"][1]["roles"] == ["workstation", "agent-worker"]
    finally:
        server.shutdown()
        server.server_close()


def test_offline_node_is_reported_with_exact_error_and_503_health():
    server, base = serve(lambda: status(worker_online=False))
    try:
        code, body = request(base + "/platform/v1/fabric/health")
        assert code == 503
        assert body["state"] == "degraded"
        assert body["findings"] == [
            {
                "code": "node_offline",
                "severity": "error",
                "message": "worker offline since 2026-09-25T15:55:00+00:00 (offline_seconds=300)",
                "node": "worker",
            }
        ]

        code, node = request(base + "/platform/v1/fabric/nodes/worker")
        assert code == 200
        assert node["state"] == "offline"
        assert node["offline_seconds"] == 300
        assert node["fabric_state"] == "degraded"
        assert node["findings"][0]["code"] == "node_offline"
    finally:
        server.shutdown()
        server.server_close()


def test_stale_snapshot_and_unavailable_status_are_preserved():
    server, base = serve(lambda: status(captured_at=NOW - timedelta(hours=1)))
    try:
        code, body = request(base + "/platform/v1/fabric/health")
        assert code == 503
        assert body["state"] == "stale"
        assert body["findings"][0]["code"] == "status_snapshot_stale"
        code, nodes = request(base + "/platform/v1/fabric/nodes")
        assert code == 200
        assert {node["state"] for node in nodes["nodes"]} == {"unverified"}
        assert nodes["findings"][0]["code"] == "status_snapshot_stale"
    finally:
        server.shutdown()
        server.server_close()

    def failing(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "failed to connect to local tailscaled")

    server, base = serve(lambda: read_status_command(runner=failing))
    try:
        code, body = request(base + "/platform/v1/fabric/nodes")
        assert code == 503
        assert body["state"] == "unavailable"
        assert body["findings"] == [
            {
                "code": "tailscale_status_failed",
                "severity": "error",
                "message": "exit 1: failed to connect to local tailscaled",
                "node": None,
            }
        ]
        code, node = request(base + "/platform/v1/fabric/nodes/mc")
        assert code == 503
        assert node["state"] == "unverified"
        assert node["fabric_findings"][0]["code"] == "tailscale_status_failed"
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_node_route_and_method_are_rejected():
    server, base = serve(status)
    try:
        assert request(base + "/platform/v1/fabric/nodes/ghost") == (
            404,
            {"error": "node_not_in_inventory", "hostname": "ghost"},
        )
        assert request(base + "/platform/v1/fabric/nope")[0] == 404
        assert request(base + "/platform/v1/fabric/health", method="POST") == (
            405,
            {"error": "method_not_allowed", "read_only": True},
        )
        assert request(base + "/platform/v1/fabric/nodes/mc", method="DELETE")[0] == 405
    finally:
        server.shutdown()
        server.server_close()


def test_policy_validation_endpoint_valid_and_invalid():
    server, base = serve(status)
    try:
        code, body = request(base + "/platform/v1/fabric/policy/validation")
        assert code == 200
        assert body["valid"] is True
        assert body["grants_checked"] == 3
    finally:
        server.shutdown()
        server.server_close()

    policy = load_policy_file(CONFIG / "tailscale.policy-plan.json")
    policy["grants"].append({"src": ["tag:agent-worker"], "dst": ["tag:apps"], "ip": ["tcp:443"]})
    server, base = serve(status, policy=policy)
    try:
        code, body = request(base + "/platform/v1/fabric/policy/validation")
        assert code == 422
        assert body["valid"] is False
        assert "agent_worker_production_access" in {item["code"] for item in body["findings"]}
        assert body["excess_flows"] == ["tag:agent-worker -> tag:apps tcp:443"]
    finally:
        server.shutdown()
        server.server_close()


def test_runtime_readback_reports_private_bind_and_summaries():
    server, base = serve(lambda: status(worker_online=False))
    try:
        code, body = request(base + "/platform/v1/fabric/runtime")
        assert code == 200
        assert body["public_endpoint_required"] is False
        assert body["read_only"] is True
        assert body["bind"]["private"] is True
        assert body["bind"]["host"] == "127.0.0.1"
        assert body["health"]["state"] == "degraded"
        assert body["health"]["errors"][0]["code"] == "node_offline"
        assert body["policy"] == {"valid": True, "grants_checked": 3, "error_codes": []}
    finally:
        server.shutdown()
        server.server_close()


def test_server_refuses_public_bind():
    runtime = FabricRuntime(INVENTORY, {}, status)
    with pytest.raises(FabricConfigError, match="public_bind_forbidden"):
        FabricAPI(runtime).server("0.0.0.0", 0)


def test_runtime_caches_status_reads():
    calls = []
    clock = {"now": NOW}

    def reader():
        calls.append(1)
        return status()

    runtime = FabricRuntime(INVENTORY, {}, reader, clock=lambda: clock["now"], cache_seconds=5)
    runtime.health()
    runtime.health()
    assert len(calls) == 1
    clock["now"] = NOW + timedelta(seconds=6)
    runtime.health()
    assert len(calls) == 2


def run_cli(*args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "mission_control.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fabric_cli_policy_and_health_from_snapshot(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    result = run_cli("fabric-policy-validate", cwd=repo)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True

    inventory = tmp_path / "nodes.json"
    inventory.write_text(
        json.dumps(
            {
                "tailnet": "t.ts.net",
                "nodes": [
                    {
                        "hostname": "mc",
                        "dns": "mc.t.ts.net.",
                        "ipv4": "100.76.208.87",
                        "roles": ["mission-control"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "status.json"
    snapshot.write_text(json.dumps(status().payload), encoding="utf-8")
    result = run_cli(
        "fabric-health",
        "--inventory",
        str(inventory),
        "--status-json",
        str(snapshot),
        cwd=tmp_path,
    )
    body = json.loads(result.stdout)
    # worker is on the tailnet but not in this inventory: warning only, still healthy.
    assert result.returncode == 0, body
    assert body["state"] == "healthy"
    assert body["counts"] == {"online": 1, "unexpected": 1}
    assert not (tmp_path / ".runtime").exists()

    result = run_cli(
        "fabric-health",
        "--inventory",
        str(inventory),
        "--status-json",
        str(tmp_path / "absent.json"),
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["findings"][0]["code"] == "status_snapshot_missing"


def test_fabric_cli_refuses_public_bind(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    result = run_cli("serve-fabric-api", "--host", "0.0.0.0", "--port", "0", cwd=repo)
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"].startswith("public_bind_forbidden: 0.0.0.0")
