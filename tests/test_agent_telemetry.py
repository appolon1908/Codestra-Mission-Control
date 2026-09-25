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


def test_unreachable_telemetry_endpoint_does_not_raise(tmp_path):
    store = make_store(tmp_path)
    emitter = AgentTelemetryEmitter(
        store,
        endpoint="http://127.0.0.1:1/platform/v1/agents/launch-events",
        timeout_seconds=0.1,
        host_name="test-host",
    )

    payload = emitter.record_launch(assignment(), execution())

    assert payload["delivery_state"] == "FAILED"
    assert payload["delivery_error_type"]
    row = store.list_agent_launches()[0]
    persisted = json.loads(row["payload_json"])
    assert persisted["delivery_state"] == "FAILED"


def test_health_endpoint_is_available_on_runtime_server(tmp_path):
    store = make_store(tmp_path)
    server = AgentTelemetryAPI(store).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3) as response:
            body = json.load(response)
        assert response.status == 200
        assert body == {"status": "ok"}
    finally:
        server.shutdown()
        server.server_close()


def test_api_requires_bearer_token_when_configured(tmp_path):
    store = make_store(tmp_path)
    server = AgentTelemetryAPI(store, bearer_token="secret-token").server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/platform/v1/agents/launch-events"

    try:
        try:
            urllib.request.urlopen(url, timeout=3)
            raise AssertionError("unauthorized request unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        req = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer secret-token"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            body = json.load(response)
        assert response.status == 200
        assert body["count"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_post_rejects_non_json_and_sets_security_headers(tmp_path):
    store = make_store(tmp_path)
    server = AgentTelemetryAPI(store).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/platform/v1/agents/launch-events"

    req = urllib.request.Request(
        url,
        data=b"not-json",
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    try:
        try:
            urllib.request.urlopen(req, timeout=3)
            raise AssertionError("unsupported media type unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 415
            assert exc.headers["Cache-Control"] == "no-store"
            assert exc.headers["X-Content-Type-Options"] == "nosniff"
            assert exc.headers["X-Request-ID"]
    finally:
        server.shutdown()
        server.server_close()


def test_health_response_sets_security_headers(tmp_path):
    store = make_store(tmp_path)
    server = AgentTelemetryAPI(store).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3) as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["X-Request-ID"]
    finally:
        server.shutdown()
        server.server_close()


def test_worker_readiness_endpoint_is_authenticated_and_machine_readable(tmp_path):
    store = make_store(tmp_path)
    payload = {
        "host": "worker-1",
        "ready": False,
        "checks": [{"name": "codex", "ok": False, "detail": "Not logged in"}],
    }
    server = AgentTelemetryAPI(
        store,
        bearer_token="secret-token",
        readiness_provider=lambda: payload,
    ).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/platform/v1/workers/readiness"

    try:
        try:
            urllib.request.urlopen(url, timeout=3)
            raise AssertionError("unauthorized readiness request unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        req = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer secret-token"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            body = json.load(response)
        assert response.status == 200
        assert body == payload
    finally:
        server.shutdown()
        server.server_close()
