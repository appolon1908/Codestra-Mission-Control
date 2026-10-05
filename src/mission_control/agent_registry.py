from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    provider: str
    agent_type: str
    display_name: str
    skills: frozenset[str] = frozenset()
    enabled: bool = True


class AgentRegistry:
    """Canonical provider/agent/lane presence registry for every coding agent."""

    def __init__(self, store) -> None:
        self.store = store

    def initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_registry (
                agent_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                agent_type TEXT NOT NULL,
                display_name TEXT NOT NULL,
                skills_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_lane_presence (
                agent_id TEXT PRIMARY KEY REFERENCES agent_registry(agent_id) ON DELETE CASCADE,
                repository TEXT,
                area_id TEXT,
                subarea_id TEXT,
                mission_id TEXT,
                task_id TEXT,
                branch TEXT,
                worktree TEXT,
                workstation TEXT,
                state TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                lease_expires_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_agent_lane_repo
                ON agent_lane_presence(repository, area_id, subarea_id);
            CREATE INDEX IF NOT EXISTS idx_agent_lane_task
                ON agent_lane_presence(task_id, state);
            """)

    def register(self, identity: AgentIdentity) -> None:
        now = _now()
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO agent_registry
                (agent_id,provider,agent_type,display_name,skills_json,enabled,registered_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(agent_id) DO UPDATE SET
                provider=excluded.provider,agent_type=excluded.agent_type,
                display_name=excluded.display_name,skills_json=excluded.skills_json,
                enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (
                    identity.agent_id,
                    identity.provider,
                    identity.agent_type,
                    identity.display_name,
                    json.dumps(sorted(identity.skills)),
                    int(identity.enabled),
                    now,
                    now,
                ),
            )

    def heartbeat(
        self,
        agent_id: str,
        *,
        state: str,
        repository: str | None = None,
        area_id: str | None = None,
        subarea_id: str | None = None,
        mission_id: str | None = None,
        task_id: str | None = None,
        branch: str | None = None,
        worktree: str | None = None,
        workstation: str | None = None,
        lease_expires_at: str | None = None,
    ) -> None:
        with self.store.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM agent_registry WHERE agent_id=?", (agent_id,)
            ).fetchone():
                raise KeyError(f"unregistered agent: {agent_id}")
            conn.execute(
                """INSERT INTO agent_lane_presence
                (agent_id,repository,area_id,subarea_id,mission_id,task_id,branch,worktree,
                 workstation,state,heartbeat_at,lease_expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(agent_id) DO UPDATE SET
                repository=excluded.repository,area_id=excluded.area_id,
                subarea_id=excluded.subarea_id,mission_id=excluded.mission_id,
                task_id=excluded.task_id,branch=excluded.branch,worktree=excluded.worktree,
                workstation=excluded.workstation,state=excluded.state,
                heartbeat_at=excluded.heartbeat_at,lease_expires_at=excluded.lease_expires_at""",
                (
                    agent_id,
                    repository,
                    area_id,
                    subarea_id,
                    mission_id,
                    task_id,
                    branch,
                    worktree,
                    workstation,
                    state,
                    _now(),
                    lease_expires_at,
                ),
            )

    def lanes(self) -> list[dict]:
        """Return registered agents with proof-derived live state; stale presence never means LIVE."""
        now = datetime.now(UTC)
        with self.store.connection() as conn:
            rows = conn.execute("""SELECT r.agent_id,r.provider,r.agent_type,r.display_name,r.skills_json,r.enabled,
             p.repository,p.area_id,p.subarea_id,p.mission_id,p.task_id,p.branch,p.worktree,p.workstation,p.state as declared_state,
             p.heartbeat_at,p.lease_expires_at FROM agent_registry r LEFT JOIN agent_lane_presence p ON p.agent_id=r.agent_id
             ORDER BY r.provider,r.agent_id""").fetchall()
            executions = {
                r["agent_id"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM agent_executions WHERE state IN ('RUNNING','WORKING','STARTED') ORDER BY updated_at"
                ).fetchall()
            }
            try:
                leases = {
                    r["agent_id"]: dict(r)
                    for r in conn.execute(
                        "SELECT * FROM work_leases WHERE state IN ('ACTIVE','LEASED','WORKING')"
                    ).fetchall()
                }
            except Exception:
                leases = {}
        out = []
        for row in rows:
            x = dict(row)
            hb = x.get("heartbeat_at")
            fresh = False
            if hb:
                try:
                    fresh = now - datetime.fromisoformat(hb) <= timedelta(seconds=90)
                except ValueError:
                    pass
            ex = executions.get(x["agent_id"])
            lease = leases.get(x["agent_id"])
            pid_alive = False
            if ex and ex.get("runner_pid"):
                try:
                    os.kill(int(ex["runner_pid"]), 0)
                    pid_alive = True
                except (OSError, ValueError):
                    pass
            x["live"] = bool(
                fresh and ex and lease and pid_alive and x.get("branch") and x.get("worktree")
            )
            x["state"] = "LIVE" if x["live"] else ("IDLE" if fresh else "OFFLINE")
            x["execution_id"] = ex.get("execution_id") if ex else None
            x["lease_id"] = lease.get("lease_id") if lease else None
            x["pid_alive"] = pid_alive
            x["heartbeat_fresh"] = fresh
            x["skills"] = json.loads(x.pop("skills_json"))
            out.append(x)
        return out
