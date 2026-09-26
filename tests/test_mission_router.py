from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mission_control.mission_router import (
    MissionRouter,
    RouterConflict,
    RouterLeaseError,
)
from mission_control.router_api import MissionRouterAPI
from mission_control.store import MissionStore


def load_middleware_hierarchy() -> list[dict]:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "config" / "router" / "middleware.v1.json").read_text(encoding="utf-8")
    )
    return payload["hierarchy"]


def make_router(tmp_path) -> MissionRouter:
    store = MissionStore(tmp_path / "mission.db")
    router = MissionRouter(store)
    router.initialize()
    router.register_hierarchy("Middleware-", load_middleware_hierarchy())
    router.create_atomic_task(
        task_id="MW-API-001",
        repository="Middleware-",
        area_id="middleware.api-surface",
        subarea_id="middleware.api.routes",
        mission_id="PAS-300",
        title="Implement dashboard read route",
        atomic_contract={
            "scope": ["src/mission_control/router_api.py"],
            "acceptance": ["focused test green"],
        },
        priority=10,
    )
    router.create_atomic_task(
        task_id="MW-CMD-001",
        repository="Middleware-",
        area_id="middleware.command-core",
        subarea_id="middleware.command.model",
        mission_id="PAS-301",
        title="Implement command envelope validation",
        atomic_contract={
            "scope": ["src/middleware/command.py"],
            "acceptance": ["unit tests green"],
        },
        priority=20,
    )
    return router


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_middleware_has_ten_stable_areas(tmp_path):
    router = make_router(tmp_path)
    areas = router.hierarchy("Middleware-")
    assert [area["name"] for area in areas] == [
        "API Surface",
        "Identity & Security",
        "Command Core",
        "Workers & Execution",
        "Adapters",
        "Persistence",
        "Observability",
        "Audit",
        "Testing & Quality",
        "Delivery",
    ]
    assert all(area["subareas"] for area in areas)


def test_atomic_task_claim_heartbeat_and_exclusive_lease(tmp_path):
    router = make_router(tmp_path)
    lease = router.claim_task("MW-API-001", "codex-builder-01", ttl_seconds=60)
    assert lease.task_id == "MW-API-001"
    assert lease.takeover is False

    with pytest.raises(RouterConflict):
        router.claim_task("MW-API-001", "claude-builder-02", ttl_seconds=60)

    renewed = router.heartbeat("MW-API-001", lease.lease_token, ttl_seconds=120)
    assert renewed.agent_id == "codex-builder-01"
    assert router.task("MW-API-001")["work_state"] == "WORKING"


def test_idle_lease_is_reclaimed_and_task_becomes_ready(tmp_path):
    router = make_router(tmp_path)
    lease = router.claim_task("MW-API-001", "codex-builder-01", ttl_seconds=60)
    with router.store.connection() as conn:
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        conn.execute(
            "UPDATE router_task_leases SET expires_at=? WHERE task_id=?",
            (expired, "MW-API-001"),
        )

    reclaimed = router.reclaim_expired()
    assert reclaimed == ["MW-API-001"]
    assert router.task("MW-API-001")["work_state"] == "READY"

    with pytest.raises(RouterLeaseError):
        router.heartbeat("MW-API-001", lease.lease_token)


def test_move_agents_only_claims_safe_ready_atomic_tasks(tmp_path):
    router = make_router(tmp_path)
    assignments = router.move_agents(
        ["codex-builder-01", "claude-builder-02"],
        repository="Middleware-",
        ttl_seconds=60,
    )
    assert [lease.task_id for lease in assignments] == ["MW-API-001", "MW-CMD-001"]
    assert len({lease.agent_id for lease in assignments}) == 2

    second = router.move_agents(
        ["codex-builder-01", "reviewer-01"],
        repository="Middleware-",
        ttl_seconds=60,
    )
    assert second == []


def test_work_progress_and_certified_progress_are_independent(tmp_path):
    router = make_router(tmp_path)
    lease = router.claim_task("MW-API-001", "codex-builder-01", ttl_seconds=60)
    router.complete_work("MW-API-001", lease.lease_token, head_sha="abc123")

    dashboard = router.dashboard("Middleware-")
    assert dashboard["progress"]["work_in_progress_pct"] == 50.0
    assert dashboard["progress"]["certified_pct"] == 0.0

    observed = router.record_reconciliation(
        "MW-API-001",
        head_sha="abc123",
        pr_merged=True,
        ci_green=True,
        post_merge_green=False,
        pr_url="https://github.com/example/repo/pull/1",
        ci_url="https://github.com/example/repo/actions/runs/1",
    )
    assert observed["work_state"] == "MERGED"
    assert observed["certified"] is False
    assert router.dashboard("Middleware-")["progress"]["certified_pct"] == 0.0

    certified = router.record_reconciliation(
        "MW-API-001",
        head_sha="abc123",
        pr_merged=True,
        ci_green=True,
        post_merge_green=True,
    )
    assert certified["work_state"] == "CERTIFIED"
    assert certified["certified"] is True
    assert router.dashboard("Middleware-")["progress"]["certified_pct"] == 50.0


def test_reconciliation_rejects_wrong_exact_head(tmp_path):
    router = make_router(tmp_path)
    lease = router.claim_task("MW-API-001", "codex-builder-01", ttl_seconds=60)
    router.complete_work("MW-API-001", lease.lease_token, head_sha="expected")

    with pytest.raises(RouterConflict):
        router.record_reconciliation(
            "MW-API-001",
            head_sha="different",
            pr_merged=True,
            ci_green=True,
            post_merge_green=True,
        )


def test_router_api_reads_then_claim_move_and_reconcile(tmp_path):
    router = make_router(tmp_path)
    server = MissionRouterAPI(router).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    with urllib.request.urlopen(
        base + "/platform/v1/router/dashboard?repository=Middleware-", timeout=3
    ) as response:
        dashboard = json.load(response)
    assert dashboard["totals"]["atomic_tasks"] == 2

    with urllib.request.urlopen(
        base + "/platform/v1/router/repositories/Middleware-/hierarchy", timeout=3
    ) as response:
        hierarchy = json.load(response)
    assert hierarchy["area_count"] == 10

    status, claim = post_json(
        base + "/platform/v1/router/tasks/MW-API-001/claim",
        {"agent_id": "codex-builder-01", "ttl_seconds": 60},
    )
    assert status == 201

    status, completed = post_json(
        base + "/platform/v1/router/tasks/MW-API-001/complete",
        {"lease_token": claim["lease_token"], "head_sha": "api-head"},
    )
    assert status == 200
    assert completed["work_state"] == "IN_REVIEW"

    status, reconciled = post_json(
        base + "/platform/v1/router/reconcile",
        {
            "task_id": "MW-API-001",
            "head_sha": "api-head",
            "pr_merged": True,
            "ci_green": True,
            "post_merge_green": True,
        },
    )
    assert status == 200
    assert reconciled["certified"] is True

    status, moved = post_json(
        base + "/platform/v1/router/move-agents",
        {
            "agent_ids": ["claude-builder-02"],
            "repository": "Middleware-",
            "ttl_seconds": 60,
        },
    )
    assert status == 200
    assert moved["assigned_count"] == 1
    assert moved["assigned"][0]["task_id"] == "MW-CMD-001"
    server.shutdown()
