from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest
from test_scheduler import FakeGit, build_scheduler, setup_store

from mission_control.git_executor import RepositoryState
from mission_control.lease import LeaseManager
from mission_control.models import MissionStatus
from mission_control.scheduler import WorkerSlot
from mission_control.watchdog import (
    LEVEL_ESCALATED,
    LEVEL_OWNER_DECISION,
    LEVEL_REMINDER,
    EscalationPolicy,
    WatchdogMonitor,
)
from mission_control.watchdog_api import WatchdogAPI

POLICY = EscalationPolicy(
    stale_heartbeat_seconds=120,
    escalate_after_seconds=600,
    owner_decision_after_seconds=1800,
)
WORKERS = (
    WorkerSlot("codex-01", "codex"),
    WorkerSlot("claude-01", "claude"),
    WorkerSlot("codex-02", "codex", enabled=False),
)


def _ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _set_lease_times(store, mission_id, *, heartbeat_ago, expires_in):
    with store.connection() as conn:
        conn.execute(
            "UPDATE leases SET heartbeat_at=?, expires_at=? WHERE mission_id=?",
            (
                _ago(heartbeat_ago),
                (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
                mission_id,
            ),
        )


def _set_mission_updated(store, mission_id, seconds_ago):
    with store.connection() as conn:
        conn.execute(
            "UPDATE missions SET updated_at=? WHERE mission_id=?",
            (_ago(seconds_ago), mission_id),
        )


def _insert_execution(store, mission_id, agent_id, execution_id, state="RUNNING"):
    now = datetime.now(UTC).isoformat()
    with store.connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_executions (
              execution_id, mission_id, agent_id, provider, state, runner_pid,
              worktree, command_json, stdout_path, stderr_path, result_path,
              started_at, updated_at
            ) VALUES (?, ?, ?, 'codex', ?, 4242, ?, '[]', '/o', '/e', '/r', ?, ?)
            """,
            (execution_id, mission_id, agent_id, state, f"/wt/{mission_id}", now, now),
        )


def _monitor(store):
    return WatchdogMonitor(store, workers=WORKERS, policy=POLICY)


def test_active_workers_report_lease_heartbeat_and_execution(tmp_path):
    store = setup_store(tmp_path, 3)
    leases = LeaseManager(store)
    leases.claim("M-0", "codex-01", ttl_seconds=600)
    leases.claim("M-1", "claude-01", ttl_seconds=600)
    leases.claim("M-2", "manual-agent", ttl_seconds=600)
    _insert_execution(store, "M-0", "codex-01", "exec-m0")
    _set_lease_times(store, "M-1", heartbeat_ago=300, expires_in=300)

    view = _monitor(store).active_workers()
    slots = {slot["agent_id"]: slot for slot in view["workers"]}

    assert slots["codex-01"]["state"] == "ACTIVE"
    assert slots["codex-01"]["leases"][0]["execution"]["execution_id"] == "exec-m0"
    assert slots["codex-01"]["leases"][0]["execution"]["runner_pid"] == 4242
    assert slots["claude-01"]["state"] == "STALE"
    assert slots["codex-02"]["state"] == "DISABLED"
    assert view["active"] == 2
    assert [lease["agent_id"] for lease in view["unmanaged_leases"]] == ["manual-agent"]


def test_expired_lease_leaves_worker_idle_and_is_reported_stale(tmp_path):
    store = setup_store(tmp_path, 1)
    LeaseManager(store).claim("M-0", "codex-01", ttl_seconds=600)
    _set_lease_times(store, "M-0", heartbeat_ago=900, expires_in=-60)
    monitor = _monitor(store)

    slots = {slot["agent_id"]: slot for slot in monitor.active_workers()["workers"]}
    stale = monitor.stale_leases()

    assert slots["codex-01"]["state"] == "IDLE"
    assert [(item["mission_id"], item["state"]) for item in stale] == [("M-0", "EXPIRED")]


def test_stale_heartbeat_escalation_lifecycle(tmp_path):
    store = setup_store(tmp_path, 1)
    leases = LeaseManager(store)
    leases.claim("M-0", "codex-01", ttl_seconds=3600)
    monitor = _monitor(store)

    _set_lease_times(store, "M-0", heartbeat_ago=150, expires_in=3000)
    first = monitor.evaluate_escalations()
    assert len(first["opened"]) == 1
    escalation_id = first["opened"][0]
    [item] = monitor.escalations()
    assert (item["kind"], item["level"], item["state"]) == (
        "STALE_HEARTBEAT",
        LEVEL_REMINDER,
        "OPEN",
    )
    assert item["delivery"] == "LOCAL_ONLY"

    acked = store.acknowledge_watchdog_escalation(escalation_id, "ralph")
    assert acked["state"] == "ACKNOWLEDGED"

    # Same level keeps the acknowledgement; a higher level re-opens it.
    assert monitor.evaluate_escalations()["raised"] == []
    assert monitor.escalations()[0]["state"] == "ACKNOWLEDGED"
    _set_lease_times(store, "M-0", heartbeat_ago=120 + 700, expires_in=3000)
    assert monitor.evaluate_escalations()["raised"] == [escalation_id]
    [item] = monitor.escalations()
    assert (item["level"], item["level_name"], item["state"]) == (
        LEVEL_ESCALATED,
        "ESCALATED",
        "OPEN",
    )

    leases.heartbeat("M-0", "codex-01", ttl_seconds=3600)
    assert monitor.evaluate_escalations()["resolved"] == [escalation_id]
    assert monitor.escalations() == []
    event_types = [row["event_type"] for row in store.events("M-0")]
    for expected in (
        "ESCALATION_OPENED",
        "ESCALATION_ACKNOWLEDGED",
        "ESCALATION_RAISED",
        "ESCALATION_RESOLVED",
    ):
        assert expected in event_types
    with pytest.raises(ValueError):
        store.acknowledge_watchdog_escalation(escalation_id, "ralph")


def test_blocked_lane_reports_blockers_and_escalates_by_age(tmp_path):
    store = setup_store(tmp_path, 2)
    store.record_checkpoint(
        "M-0",
        "codex-01",
        "BLOCKED",
        head_sha="def456",
        dirty_count=0,
        tests={},
        blockers=["waiting on credentials"],
        next_task_requested=False,
    )
    store.set_status("M-0", MissionStatus.BLOCKED)
    store.set_status("M-1", MissionStatus.NEEDS_DECISION)
    _set_mission_updated(store, "M-0", 2000)
    monitor = _monitor(store)

    lanes = monitor.blocked_lanes()
    assert [lane["mission_id"] for lane in lanes] == ["M-0", "M-1"]
    assert lanes[0]["last_checkpoint"]["blockers"] == ["waiting on credentials"]

    monitor.evaluate_escalations()
    by_mission = {item["mission_id"]: item for item in monitor.escalations()}
    assert by_mission["M-0"]["kind"] == "BLOCKED_LANE"
    assert by_mission["M-0"]["level"] == LEVEL_OWNER_DECISION
    assert by_mission["M-0"]["detail"]["blockers"] == ["waiting on credentials"]
    assert by_mission["M-1"]["kind"] == "NEEDS_DECISION_LANE"
    assert by_mission["M-1"]["level"] == LEVEL_REMINDER

    store.set_status("M-0", MissionStatus.READY)
    resolved = monitor.evaluate_escalations()["resolved"]
    assert resolved == [by_mission["M-0"]["id"]]


def test_tick_records_successor_dispatch_evidence(tmp_path):
    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)
    LeaseManager(store).claim("M-0", "old-agent", ttl_seconds=600)
    _insert_execution(store, "M-0", "old-agent", "old-exec", state="LOST")
    store.record_checkpoint(
        "M-0",
        "old-agent",
        "IMPLEMENTED",
        head_sha="abc123",
        dirty_count=0,
        tests={},
        blockers=[],
        next_task_requested=False,
    )
    _set_lease_times(store, "M-0", heartbeat_ago=900, expires_in=-1)

    snapshot = scheduler.tick()

    [outcome] = snapshot.assigned
    assert outcome.takeover is True
    assert outcome.predecessor_agent_id == "old-agent"
    assert outcome.predecessor_execution_id == "old-exec"
    assert outcome.dispatch_id is not None
    [dispatch] = scheduler.monitor.dispatches(takeover_only=True)
    assert dispatch["id"] == outcome.dispatch_id
    assert dispatch["agent_id"] == outcome.agent_id != "old-agent"
    assert dispatch["execution_id"] == outcome.execution_id
    assert dispatch["checkpoint_head"] == "abc123"
    assert dispatch["state"] == "RUNNING"
    events = [row["event_type"] for row in store.events("M-0")]
    assert "SUCCESSOR_DISPATCHED" in events
    # Successor holds a fresh lease, so no stale/expired escalation stays open.
    assert scheduler.monitor.escalations() == []


def test_dirty_takeover_is_recorded_and_escalated_as_needs_decision(tmp_path):
    class DirtyGit(FakeGit):
        def inspect(self, repo):
            return RepositoryState(
                path=str(repo),
                branch="mission/x",
                head_sha="abc123",
                dirty_count=2,
                upstream=None,
                ahead=None,
                behind=None,
            )

    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)
    scheduler.git = DirtyGit()
    LeaseManager(store).claim("M-0", "old-agent", ttl_seconds=600)
    _insert_execution(store, "M-0", "old-agent", "old-exec", state="LOST")
    _set_lease_times(store, "M-0", heartbeat_ago=900, expires_in=-1)

    snapshot = scheduler.tick()

    [outcome] = snapshot.assigned
    assert outcome.state == "NOT_DISPATCHED"
    assert outcome.predecessor_execution_id == "old-exec"
    assert store.get_mission("M-0")["status"] == MissionStatus.NEEDS_DECISION.value
    [dispatch] = scheduler.monitor.dispatches(mission_id="M-0")
    assert dispatch["takeover"] is True
    assert "dirty expired worktree" in dispatch["reason"]
    kinds = [item["kind"] for item in scheduler.monitor.escalations()]
    assert kinds == ["NEEDS_DECISION_LANE"]


def test_failed_fresh_dispatch_opens_dispatch_failed_escalation(tmp_path):
    store = setup_store(tmp_path, 1)
    store.upsert_repository(
        "repo",
        full_name="owner/repo",
        local_path=None,
        origin_url=None,
        default_branch="main",
        visibility="private",
        local_present=False,
        mission_channel_path=None,
        workspace_path=None,
    )
    scheduler = build_scheduler(store, tmp_path)

    snapshot = scheduler.tick()

    assert snapshot.blocked and "repository not local" in snapshot.blocked[0]
    assert snapshot.escalations is not None and len(snapshot.escalations["opened"]) == 1
    [item] = scheduler.monitor.escalations()
    assert item["kind"] == "DISPATCH_FAILED"
    assert "repository not local" in item["detail"]["reason"]


def test_lease_conflict_during_dispatch_does_not_crash_tick(tmp_path):
    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)
    original = scheduler._eligible_missions

    def racing_candidates():
        candidates = original()
        LeaseManager(store).claim("M-0", "racing-agent", ttl_seconds=600)
        return candidates

    scheduler._eligible_missions = racing_candidates
    snapshot = scheduler.tick()

    [outcome] = snapshot.assigned
    assert outcome.state == "NOT_DISPATCHED"
    assert "LeaseConflict" in outcome.reason
    assert LeaseManager(store).current("M-0")["agent_id"] == "racing-agent"
    assert scheduler.monitor.escalations() == []


@pytest.fixture
def api_server(tmp_path):
    servers = []

    def start(store, **kwargs):
        monitor = kwargs.pop("monitor", None) or _monitor(store)
        server = WatchdogAPI(monitor, **kwargs).server("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _call(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_api_readback_endpoints(tmp_path, api_server):
    store = setup_store(tmp_path, 3)
    leases = LeaseManager(store)
    leases.claim("M-0", "codex-01", ttl_seconds=600)
    leases.claim("M-1", "claude-01", ttl_seconds=600)
    _set_lease_times(store, "M-1", heartbeat_ago=200, expires_in=400)
    store.set_status("M-2", MissionStatus.BLOCKED)
    base = api_server(store)

    status, health = _call(base, "GET", "/health")
    assert status == 200 and health["external_delivery"] == "DISABLED"
    assert health["dispatch_enabled"] is False

    status, workers = _call(base, "GET", "/platform/v1/watchdog/workers")
    assert status == 200 and workers["active"] == 2

    status, leases_view = _call(base, "GET", "/platform/v1/watchdog/leases")
    assert status == 200
    assert [(item["mission_id"], item["state"]) for item in leases_view["items"]] == [
        ("M-1", "STALE")
    ]
    status, all_leases = _call(base, "GET", "/platform/v1/watchdog/leases?state=active,stale")
    assert status == 200 and all_leases["count"] == 2
    status, error = _call(base, "GET", "/platform/v1/watchdog/leases?state=bogus")
    assert status == 400 and error["error"] == "invalid_state"

    status, lanes = _call(base, "GET", "/platform/v1/watchdog/blocked-lanes")
    assert status == 200 and [lane["mission_id"] for lane in lanes["items"]] == ["M-2"]

    status, evaluation = _call(base, "POST", "/platform/v1/watchdog/escalations/evaluate", {})
    assert status == 200 and len(evaluation["opened"]) == 2

    status, escalations = _call(base, "GET", "/platform/v1/watchdog/escalations")
    assert status == 200
    assert sorted(item["kind"] for item in escalations["items"]) == [
        "BLOCKED_LANE",
        "STALE_HEARTBEAT",
    ]
    status, filtered = _call(base, "GET", "/platform/v1/watchdog/escalations?mission_id=M-2")
    assert status == 200 and filtered["count"] == 1
    escalation_id = filtered["items"][0]["id"]

    path = f"/platform/v1/watchdog/escalations/{escalation_id}/acknowledge"
    status, error = _call(base, "POST", path, {})
    assert status == 400 and error["fields"] == ["actor"]
    status, acked = _call(base, "POST", path, {"actor": "ralph"})
    assert status == 200 and acked["state"] == "ACKNOWLEDGED"
    assert acked["acknowledged_by"] == "ralph"
    status, error = _call(
        base, "POST", "/platform/v1/watchdog/escalations/9999/acknowledge", {"actor": "x"}
    )
    assert status == 404 and error["error"] == "escalation_not_found"

    store.set_status("M-2", MissionStatus.READY)
    _call(base, "POST", "/platform/v1/watchdog/escalations/evaluate", {})
    status, error = _call(base, "POST", path, {"actor": "ralph"})
    assert status == 409 and error["error"] == "escalation_resolved"
    status, resolved = _call(base, "GET", "/platform/v1/watchdog/escalations?state=resolved")
    assert status == 200 and [item["id"] for item in resolved["items"]] == [escalation_id]

    status, snapshot = _call(base, "GET", "/platform/v1/watchdog/snapshot")
    assert status == 200
    assert snapshot["policy"]["stale_heartbeat_seconds"] == 120
    assert [item["mission_id"] for item in snapshot["stale_leases"]] == ["M-1"]

    status, _ = _call(base, "GET", "/platform/v1/watchdog/unknown")
    assert status == 404
    status, _ = _call(base, "GET", "/platform/v1/watchdog/dispatches?limit=x")
    assert status == 400


def test_api_tick_requires_scheduler_confirmation_and_permission(tmp_path, api_server):
    store = setup_store(tmp_path, 1)
    scheduler = build_scheduler(store, tmp_path)

    read_only = api_server(store)
    status, error = _call(read_only, "POST", "/platform/v1/watchdog/tick", {"dispatch": True})
    assert status == 503 and error["error"] == "scheduler_unavailable"

    locked = api_server(store, monitor=scheduler.monitor, scheduler=scheduler)
    status, error = _call(locked, "POST", "/platform/v1/watchdog/tick", {})
    assert status == 400 and error["error"] == "dispatch_confirmation_required"
    status, error = _call(locked, "POST", "/platform/v1/watchdog/tick", {"dispatch": True})
    assert status == 403 and error["error"] == "dispatch_disabled"
    assert store.get_mission("M-0")["status"] == MissionStatus.READY.value

    enabled = api_server(
        store,
        monitor=scheduler.monitor,
        scheduler=scheduler,
        allow_dispatch=True,
    )
    status, snapshot = _call(enabled, "POST", "/platform/v1/watchdog/tick", {"dispatch": True})
    assert status == 200
    assert [item["mission_id"] for item in snapshot["assigned"]] == ["M-0"]
    dispatch_id = snapshot["assigned"][0]["dispatch_id"]

    status, dispatches = _call(enabled, "GET", "/platform/v1/watchdog/dispatches?mission_id=M-0")
    assert status == 200 and [item["id"] for item in dispatches["items"]] == [dispatch_id]
    status, takeovers = _call(enabled, "GET", "/platform/v1/watchdog/dispatches?takeover=1")
    assert status == 200 and takeovers["count"] == 0


def test_escalation_policy_validates_thresholds():
    with pytest.raises(ValueError):
        EscalationPolicy(escalate_after_seconds=900, owner_decision_after_seconds=900)
    with pytest.raises(ValueError):
        EscalationPolicy(stale_heartbeat_seconds=0)
    assert POLICY.level_for(0) == LEVEL_REMINDER
    assert POLICY.level_for(600) == LEVEL_ESCALATED
    assert POLICY.level_for(1800) == LEVEL_OWNER_DECISION
