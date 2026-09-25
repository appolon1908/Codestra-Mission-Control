from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from mission_control.adapters.base import AgentAssignment, AgentExecution
from mission_control.agent_telemetry import AgentTelemetryAPI, AgentTelemetryEmitter
from mission_control.models import Mission
from mission_control.store import MissionStore


def make_store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("M-1", "repo", "goal"))
    return store


def assignment():
    return AgentAssignment(
        mission_id="M-1",
        agent_id="codex-01",
        repository="repo",
        worktree="/worktrees/M-1",
        branch="mission/m-1",
        base_sha="abc123",
        goal="goal",
        acceptance=("tests pass",),
    )


def execution():
    return AgentExecution(
        execution_id="exec-1",
        mission_id="M-1",
        agent_id="codex-01",
        provider="codex",
        state="RUNNING",
        worktree="/worktrees/M-1",
    )


def test_launch_is_persisted(tmp_path):
    store = make_store(tmp_path)
    payload = AgentTelemetryEmitter(store, host_name="test-host").record_launch(
        assignment(), execution(), mission_level=2, complexity_class="C4"
    )
    rows = store.list_agent_launches()
    assert len(rows) == 1
    assert rows[0]["event_id"] == payload["event_id"]
    assert rows[0]["mission_id"] == "M-1"
    assert rows[0]["host"] == "test-host"


def test_api_accepts_and_returns_launch_events(tmp_path):
    store = make_store(tmp_path)
    api = AgentTelemetryAPI(store)
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/platform/v1/agents/launch-events"

    payload = {
        "event_id": "evt-1",
        "execution_id": "exec-1",
        "mission_id": "M-1",
        "agent_id": "claude-01",
        "provider": "claude",
        "role": "WRITER",
        "host": "desktop",
        "repository": "repo",
        "worktree": "/wt",
        "branch": "mission/m-1",
        "head_sha": "abc123",
        "mission_level": 1,
        "complexity_class": "C3",
        "state": "RUNNING",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        assert response.status == 201

    with urllib.request.urlopen(url, timeout=3) as response:
        body = json.load(response)
    assert body["count"] == 1
    assert body["items"][0]["payload"]["agent_id"] == "claude-01"
    server.shutdown()


def _post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        return response.status, dict(response.headers), json.load(response)


def test_api_lifecycle_active_rating_metrics_and_headers(tmp_path):
    store = make_store(tmp_path)
    api = AgentTelemetryAPI(store)
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    launch = {
        "event_id": "launch-1",
        "execution_id": "exec-1",
        "mission_id": "M-1",
        "agent_id": "codex-01",
        "provider": "codex",
        "role": "WRITER",
        "host": "desktop",
        "repository": "repo",
        "worktree": "/wt",
        "branch": "mission/m-1",
        "head_sha": "abc123",
        "mission_level": 3,
        "complexity_class": "C4",
        "state": "RUNNING",
    }
    status, headers, _ = _post_json(
        base + "/platform/v1/agents/launch-events", launch
    )
    assert status == 201
    assert headers["X-Codestra-API-Version"] == "v1"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"

    with urllib.request.urlopen(
        base + "/platform/v1/agents/active", timeout=3
    ) as response:
        active = json.load(response)
    assert active["count"] == 1
    assert active["items"][0]["agent_id"] == "codex-01"

    rating_payload = {
        "execution_id": "exec-1",
        "agent_id": "codex-01",
        "mission_id": "M-1",
        "delivery": 90,
        "quality": 95,
        "reliability": 88,
        "goal_advancement": 90,
        "speed": 82,
        "efficiency": 86,
        "complexity_class": "C4",
        "confidence": 92,
    }
    status, _, rating = _post_json(
        base + "/platform/v1/agents/ratings", rating_payload
    )
    assert status == 201
    assert rating["avi"] >= 80
    assert rating["confidence"] == 92

    work_payload = {
        "execution_id": "exec-1",
        "agent_id": "codex-01",
        "mission_id": "M-1",
        "produced_points": 24,
        "pushed_points": 20,
        "verified_points": 18,
        "rework_points": 2,
        "unpushed_commits": 1,
        "unpushed_files": 3,
        "stop_count": 0,
        "unexpected_stop_count": 0,
        "restart_count": 0,
        "stalled_minutes": 5,
        "blocked_minutes": 10,
        "active_minutes": 120,
        "current_goalpost": "G5",
        "next_goalpost": "G6",
        "mission_completion_pct": 72,
    }
    status, _, _ = _post_json(
        base + "/platform/v1/agents/work-metrics", work_payload
    )
    assert status == 201

    stop_event = {
        "event_id": "stop-1",
        "execution_id": "exec-1",
        "mission_id": "M-1",
        "agent_id": "codex-01",
        "provider": "codex",
        "event_type": "AGENT_STOPPED",
        "state": "STOPPED",
        "reason": "requested_stop",
    }
    status, _, _ = _post_json(
        base + "/platform/v1/agents/events", stop_event
    )
    assert status == 201

    with urllib.request.urlopen(
        base + "/platform/v1/agents/active", timeout=3
    ) as response:
        active_after = json.load(response)
    assert active_after["count"] == 0

    with urllib.request.urlopen(
        base + "/platform/v1/agents/summary", timeout=3
    ) as response:
        summary = json.load(response)
    assert summary == {
        "active_agents": 0,
        "failures_total": 0,
        "launches_total": 1,
        "stops_total": 1,
    }

    with urllib.request.urlopen(base + "/metrics", timeout=3) as response:
        metrics = response.read().decode()
    assert "codestra_agents_active 0" in metrics
    assert "codestra_agent_launches_total 1" in metrics
    assert "codestra_agent_stops_total 1" in metrics

    server.shutdown()


def test_api_rejects_missing_event_fields(tmp_path):
    store = make_store(tmp_path)
    api = AgentTelemetryAPI(store)
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/platform/v1/agents/events"
    req = urllib.request.Request(
        url,
        data=json.dumps({"event_id": "bad"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=3)
        assert False, "request should fail"
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read().decode())
        assert body["error"] == "missing_fields"
    server.shutdown()


def test_api_token_required_for_non_loopback_bind(tmp_path):
    store = make_store(tmp_path)
    api = AgentTelemetryAPI(store)
    try:
        api.server("0.0.0.0", 0)
    except ValueError as exc:
        assert "MISSION_CONTROL_AGENT_API_TOKEN" in str(exc)
    else:
        raise AssertionError("non-loopback bind must require API token")


def test_write_endpoints_require_bearer_token_when_configured(tmp_path):
    store = make_store(tmp_path)
    api = AgentTelemetryAPI(store, api_token="secret-token")
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/platform/v1/agents/launch-events"

    payload = {
        "event_id": "evt-token",
        "execution_id": "exec-token",
        "mission_id": "M-1",
        "agent_id": "codex-token",
        "provider": "codex",
        "role": "WRITER",
        "host": "desktop",
        "repository": "repo",
        "worktree": "/wt",
        "branch": "mission/token",
        "head_sha": "abc123",
        "mission_level": 1,
        "complexity_class": "C3",
        "state": "RUNNING",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
        assert json.load(exc)["error"] == "unauthorized"
    else:
        raise AssertionError("missing bearer token must fail")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer secret-token",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        assert response.status == 201

    server.shutdown()
