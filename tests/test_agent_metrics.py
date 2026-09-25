from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
import urllib.error

from mission_control.agent_metrics import AgentMetricsCollector
from mission_control.agent_telemetry import AgentTelemetryAPI
from mission_control.models import Mission, MissionStatus
from mission_control.store import MissionStore


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def setup_execution(tmp_path):
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", str(remote)])
    subprocess.check_call(["git", "init", "-b", "main", str(repo)])
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "file.txt").write_text("base\n")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")

    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission("M-1", "repo", "goal", status=MissionStatus.WORKING)
    )
    store.create_agent_execution(
        execution_id="exec-1",
        mission_id="M-1",
        agent_id="codex-01",
        provider="codex",
        state="RUNNING",
        runner_pid=999999,
        worktree=str(repo),
        command=["codex"],
        stdout_path=str(tmp_path / "out"),
        stderr_path=str(tmp_path / "err"),
        result_path=str(tmp_path / "result"),
    )
    launch = {
        "event_id": "launch-1",
        "event_type": "AGENT_LAUNCHED",
        "created_at": "2026-09-25T12:00:00+00:00",
        "execution_id": "exec-1",
        "mission_id": "M-1",
        "agent_id": "codex-01",
        "provider": "codex",
        "role": "WRITER",
        "host": "desktop",
        "repository": "repo",
        "worktree": str(repo),
        "branch": "main",
        "head_sha": base,
        "mission_level": 3,
        "complexity_class": "C3",
        "state": "RUNNING",
    }
    store.record_agent_launch(launch)
    store.record_agent_event(launch)
    return store, repo, base


def test_publication_refresh_velocity_goalposts_and_reliability(tmp_path):
    store, repo, base = setup_execution(tmp_path)
    (repo / "file.txt").write_text("base\nchange\n")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-m", "change")
    (repo / "dirty.txt").write_text("dirty\n")

    store.upsert_agent_work_metrics(
        {
            "execution_id": "exec-1",
            "agent_id": "codex-01",
            "mission_id": "M-1",
            "verified_points": 12,
            "rework_points": 2,
            "active_minutes": 120,
            "current_goalpost": "G4_LOCAL_TESTS",
            "next_goalpost": "G5_INTEGRATION",
            "mission_completion_pct": 48,
        }
    )
    collector = AgentMetricsCollector(store)
    publication = collector.publication("exec-1")
    assert publication["launch_head"] == base
    assert publication["commits_since_launch"] == 1
    assert publication["ahead"] == 1
    assert publication["dirty_files"] == 1
    assert publication["remote_parity"] is False

    velocity = collector.velocity("exec-1")
    assert velocity["effective_velocity_points_per_hour"] == 6.0
    assert velocity["net_velocity_points_per_hour"] == 5.0

    goalposts = collector.goalposts("exec-1")
    assert goalposts["current_goalpost"] == "G4_LOCAL_TESTS"
    assert goalposts["remaining_pct"] == 52.0

    reliability = collector.reliability("exec-1")
    assert reliability["launch_count"] == 1
    assert reliability["lost_count"] == 0

    refreshed = collector.refresh("exec-1")
    assert refreshed["work_metrics"]["unpushed_commits"] == 1
    assert refreshed["work_metrics"]["unpushed_files"] == 1


def test_dynamic_api_endpoints_and_contract(tmp_path):
    store, _repo, _ = setup_execution(tmp_path)
    store.upsert_agent_rating(
        {
            "execution_id": "exec-1",
            "agent_id": "codex-01",
            "mission_id": "M-1",
            "avi": 88.0,
            "band": "Strong",
            "confidence": 90.0,
            "provisional": False,
            "complexity_class": "C3",
            "dimensions": {"delivery": 90},
        }
    )
    api = AgentTelemetryAPI(store)
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    paths = [
        "/platform/v1/agents/executions/exec-1",
        "/platform/v1/agents/missions/M-1",
        "/platform/v1/agents/publication/exec-1",
        "/platform/v1/agents/reliability/exec-1",
        "/platform/v1/agents/velocity/exec-1",
        "/platform/v1/agents/goalposts/exec-1",
        "/platform/v1/agents/leaderboard",
        "/platform/v1/agents/stale?minutes=1",
        "/platform/v1/agents/contract",
    ]
    for path in paths:
        with urllib.request.urlopen(base + path, timeout=3) as response:
            assert response.status == 200
            assert response.headers["X-Codestra-API-Version"] == "v1"
            json.load(response)

    req = urllib.request.Request(
        base + "/platform/v1/agents/refresh/exec-1",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        refreshed = json.load(response)
    assert refreshed["publication"]["git_available"] is True

    with urllib.request.urlopen(
        base + "/platform/v1/agents/leaderboard", timeout=3
    ) as response:
        leaderboard = json.load(response)
    assert leaderboard["items"][0]["rank"] == 1
    assert leaderboard["items"][0]["avi"] == 88.0

    server.shutdown()


def test_publication_keeps_upstream_when_launch_head_is_not_ancestor(tmp_path):
    store, repo, _base = setup_execution(tmp_path)
    launch = store.get_agent_launch("exec-1")
    payload = json.loads(launch["payload_json"])
    payload["head_sha"] = "0" * 40
    payload["event_id"] = "launch-replaced"
    store.record_agent_launch(payload)

    publication = AgentMetricsCollector(store).publication("exec-1")

    assert publication["git_available"] is True
    assert publication["upstream"] == "origin/main"
    assert publication["remote_head"] is not None


def test_stale_agent_uses_latest_lifecycle_event_not_launch_time(tmp_path):
    from datetime import UTC, datetime, timedelta

    store, _repo, _base = setup_execution(tmp_path)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    with store.connection() as conn:
        conn.execute(
            "UPDATE agent_launch_events SET created_at=? WHERE execution_id=?",
            (old, "exec-1"),
        )
        conn.execute(
            "UPDATE agent_events SET created_at=? WHERE execution_id=?",
            (fresh, "exec-1"),
        )

    assert AgentMetricsCollector(store).stale(minutes=30) == []


def test_terminal_execution_runtime_stops_at_updated_at(tmp_path):
    from datetime import UTC, datetime, timedelta

    store, _repo, _base = setup_execution(tmp_path)
    started = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    ended = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with store.connection() as conn:
        conn.execute(
            """
            UPDATE agent_executions
            SET state='COMPLETED', started_at=?, updated_at=?, exit_code=0
            WHERE execution_id='exec-1'
            """,
            (started, ended),
        )

    detail = AgentMetricsCollector(store).execution_detail("exec-1")
    assert 59.9 <= detail["runtime_minutes"] <= 60.1

    refreshed = AgentMetricsCollector(store).refresh("exec-1")
    assert 59.9 <= refreshed["work_metrics"]["active_minutes"] <= 60.1


def _request_json(url, *, payload=None, method="GET"):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_api_rejects_forged_execution_identity(tmp_path):
    import urllib.error

    store, _repo, _base = setup_execution(tmp_path)
    api = AgentTelemetryAPI(store)
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    status, body = _request_json(
        base + "/platform/v1/agents/work-metrics",
        payload={
            "execution_id": "exec-1",
            "agent_id": "forged-agent",
            "mission_id": "M-1",
        },
        method="POST",
    )
    assert status == 409
    assert body["error"] == "execution_identity_mismatch"
    assert body["mismatches"]["agent_id"]["expected"] == "codex-01"

    status, body = _request_json(
        base + "/platform/v1/agents/events",
        payload={
            "event_id": "forged-event",
            "execution_id": "exec-1",
            "mission_id": "M-OTHER",
            "agent_id": "codex-01",
            "provider": "codex",
            "event_type": "AGENT_COMPLETED",
            "state": "COMPLETED",
        },
        method="POST",
    )
    assert status == 409
    assert body["error"] == "execution_identity_mismatch"
    assert body["mismatches"]["mission_id"]["expected"] == "M-1"

    server.shutdown()


def test_mission_execution_endpoint_returns_404_for_unknown_mission(tmp_path):
    import urllib.error

    store, _repo, _base = setup_execution(tmp_path)
    api = AgentTelemetryAPI(store)
    server = api.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    status, body = _request_json(
        f"http://{host}:{port}/platform/v1/agents/missions/DOES-NOT-EXIST"
    )
    assert status == 404
    assert body == {"error": "mission_not_found", "id": "DOES-NOT-EXIST"}

    server.shutdown()
