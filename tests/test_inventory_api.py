from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mission_control.git_executor import GitWorktreeExecutor
from mission_control.inventory import ExpectedHost, InventoryScanner, RepositoryTarget
from mission_control.inventory_api import InventoryAPI
from mission_control.store import MissionStore

GIT = GitWorktreeExecutor().git
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    base = [GIT, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-C", str(path)]
    subprocess.check_call([*base, "init", "-q", "-b", "main"])
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.check_call([*base, "add", "a.txt"])
    subprocess.check_call([*base, "commit", "-q", "-m", "a"])
    (path / "dirty.txt").write_text("x\n", encoding="utf-8")
    return path


@pytest.fixture()
def store(tmp_path: Path) -> MissionStore:
    store = MissionStore(tmp_path / "mc.db")
    store.initialize()
    return store


def call(base: str, method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(base + path, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture()
def serve():
    servers = []

    def start(api: InventoryAPI) -> str:
        server = api.server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def scan_runner(store: MissionStore, repo: Path, tmp_path: Path):
    scanner = InventoryScanner(store=store)
    return lambda: scanner.scan(
        repositories=[
            RepositoryTarget("app", str(repo)),
            RepositoryTarget("ghost", str(tmp_path / "ghost")),
        ],
        expected_hosts=[ExpectedHost("desktop")],
        status_provider=lambda: {"Self": {"HostName": "desktop", "Online": True}},
    )


def test_inventory_api_scan_and_read_contract(serve, store, repo, tmp_path):
    base = serve(InventoryAPI(store, scan_runner(store, repo, tmp_path)))

    assert call(base, "GET", "/health") == (
        200,
        {"ok": True, "service": "mission-control-inventory-api"},
    )
    assert call(base, "GET", "/platform/v1/inventory/drift") == (
        404,
        {"error": "no_inventory_scan"},
    )

    status, scan = call(base, "POST", "/platform/v1/inventory/drift/scans")
    assert status == 201
    assert scan["complete"] is False
    assert scan["summary"]["errors"] == [
        {
            "kind": "repository",
            "key": "ghost",
            "code": "repository_path_missing",
            "message": f"path does not exist: {tmp_path / 'ghost'}",
        }
    ]

    status, drift = call(base, "GET", "/platform/v1/inventory/drift")
    assert status == 200
    assert drift["scan_id"] == scan["scan_id"]
    assert drift["report_stale"] is False
    kinds = {item["kind"] for item in drift["items"]}
    assert kinds == {"repository", "worktree", "host"}
    worktree = next(item for item in drift["items"] if item["kind"] == "worktree")
    assert worktree["status"] == "DRIFT"
    assert set(worktree["flags"]) >= {"untracked", "local_only", "pending_push", "no_upstream"}

    status, errors = call(base, "GET", "/platform/v1/inventory/drift?status=error")
    assert status == 200
    assert [item["key"] for item in errors["items"]] == ["ghost"]
    assert errors["complete"] is False

    status, filtered = call(
        base, "GET", "/platform/v1/inventory/drift?kind=worktree&repository=app"
    )
    assert status == 200 and len(filtered["items"]) == 1
    assert filtered["summary"]["total"] == 1

    status, invalid = call(base, "GET", "/platform/v1/inventory/drift?kind=nope&status=bad")
    assert status == 400
    assert invalid == {
        "error": "invalid_filter",
        "invalid": {"kind": ["nope"], "status": ["BAD"]},
    }

    status, app = call(base, "GET", "/platform/v1/inventory/drift/repositories/app")
    assert status == 200
    assert app["repository"] == "app"
    assert {item["kind"] for item in app["items"]} == {"repository", "worktree"}

    status, ghost = call(base, "GET", "/platform/v1/inventory/drift/repositories/ghost")
    assert status == 200
    assert ghost["items"][0]["error"]["code"] == "repository_path_missing"
    assert ghost["items"][0]["last_verified"] is None

    status, unknown = call(base, "GET", "/platform/v1/inventory/drift/repositories/other")
    assert status == 404
    assert unknown["error"] == "repository_not_in_inventory"

    status, hosts = call(base, "GET", "/platform/v1/inventory/drift/hosts")
    assert status == 200
    assert [(item["key"], item["status"]) for item in hosts["items"]] == [("desktop", "OK")]

    assert call(base, "GET", "/platform/v1/nope")[0] == 404
    assert call(base, "POST", "/platform/v1/inventory/drift")[0] == 404
    assert call(base, "POST", "/platform/v1/inventory/drift/scans", b"[1]")[0] == 400
    assert call(base, "POST", "/platform/v1/inventory/drift/scans", b"{not json")[0] == 400


def test_inventory_api_rejects_concurrent_scan_and_reports_scan_failure(serve, store):
    def explode() -> dict:
        raise RuntimeError("registry unavailable")

    api = InventoryAPI(store, explode)
    base = serve(api)
    assert call(base, "POST", "/platform/v1/inventory/drift/scans") == (
        500,
        {"error": "scan_failed", "message": "RuntimeError: registry unavailable"},
    )
    api.scan_lock.acquire()
    try:
        assert call(base, "POST", "/platform/v1/inventory/drift/scans") == (
            409,
            {"error": "scan_in_progress"},
        )
    finally:
        api.scan_lock.release()


def test_cli_inventory_scan_and_drift(tmp_path, repo):
    db = tmp_path / "cli.db"
    status_file = tmp_path / "tailscale.json"
    status_file.write_text(json.dumps({"Self": {"HostName": "desktop"}}), encoding="utf-8")
    nodes = tmp_path / "nodes.json"
    nodes.write_text(
        json.dumps({"nodes": [{"hostname": "desktop"}, {"hostname": "server-2"}]}),
        encoding="utf-8",
    )
    env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"}
    cli = [sys.executable, "-m", "mission_control.cli", "--db", str(db)]

    scan = json.loads(
        subprocess.check_output(
            [
                *cli,
                "inventory-scan",
                "--repo",
                f"app={repo}",
                "--nodes-config",
                str(nodes),
                "--tailscale-status",
                str(status_file),
            ],
            env=env,
            text=True,
        )
    )
    assert scan["complete"] is True
    statuses = {(item["kind"], item["key"]): item["status"] for item in scan["items"]}
    assert statuses[("host", "desktop")] == "OK"
    assert statuses[("host", "server-2")] == "STALE"

    drift = json.loads(
        subprocess.check_output(
            [*cli, "inventory-drift", "--kind", "host", "--status", "STALE"],
            env=env,
            text=True,
        )
    )
    assert drift["scan_id"] == scan["scan_id"]
    assert [item["key"] for item in drift["items"]] == ["server-2"]

    bad = subprocess.run(
        [*cli, "inventory-scan", "--repo", "missing-separator"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode == 1
    assert "--repo must be NAME=PATH, got: missing-separator" in bad.stderr
