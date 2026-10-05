from datetime import UTC, datetime, timedelta

from mission_control.agent_registry import AgentIdentity, AgentRegistry
from mission_control.oversight import OversightStore, Supervisor
from mission_control.store import MissionStore


def setup(tmp_path):
    base = MissionStore(tmp_path / "mc.db")
    base.initialize()
    agents = AgentRegistry(base)
    agents.initialize()
    oversight = OversightStore(base)
    oversight.initialize()
    return base, agents, oversight


def test_one_supervisor_per_repo_and_control_surface(tmp_path):
    base, agents, oversight = setup(tmp_path)
    oversight.register_supervisor(Supervisor("repo-mw", "REPOSITORY", "Middleware-"))
    oversight.register_supervisor(Supervisor("vs-control", "CONTROL_SURFACE", "VS_CODE"))
    oversight.register_supervisor(Supervisor("desktop-control", "WORKSTATION", "UBUNTU_DESKTOP"))
    with base.connection() as conn:
        rows = conn.execute("SELECT * FROM supervisors ORDER BY supervisor_id").fetchall()
    assert len(rows) == 3
    assert all(row["implementation_allowed"] == 0 for row in rows)


def test_checkpoint_survives_agent_process_and_records_exact_head(tmp_path):
    base, agents, oversight = setup(tmp_path)
    checkpoint_id = oversight.checkpoint(
        agent_id="codex-01",
        repository="Middleware-",
        mission_id="M1",
        task_id="T1",
        branch="mission/t1",
        head_sha="abc123",
        dirty_count=0,
        state="SAFE_POINT",
        summary="tests green",
        evidence={"tests": 12},
    )
    with base.connection() as conn:
        row = conn.execute(
            "SELECT * FROM durable_agent_checkpoints WHERE id=?", (checkpoint_id,)
        ).fetchone()
    assert row["head_sha"] == "abc123"
    assert row["summary"] == "tests green"


def test_stale_heartbeat_creates_deduplicated_notification(tmp_path):
    base, agents, oversight = setup(tmp_path)
    agents.register(AgentIdentity("claude-01", "anthropic", "claude", "Claude"))
    agents.heartbeat(
        "claude-01", state="WORKING", repository="Middleware-", mission_id="M1", task_id="T1"
    )
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with base.connection() as conn:
        conn.execute(
            "UPDATE agent_lane_presence SET heartbeat_at=? WHERE agent_id='claude-01'", (old,)
        )
    assert oversight.scan_stale_agents(agents, stale_after_seconds=300) == ["claude-01"]
    assert oversight.scan_stale_agents(agents, stale_after_seconds=300) == ["claude-01"]
    notes = oversight.notifications()
    assert len(notes) == 1
    assert notes[0]["kind"] == "AGENT_HEARTBEAT_LOST"
