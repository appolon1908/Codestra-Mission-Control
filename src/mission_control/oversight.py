from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Supervisor:
    supervisor_id: str
    scope_type: str
    scope_key: str
    role: str = "OVERSIGHT"
    implementation_allowed: bool = False


class OversightStore:
    """Persistent supervisor, checkpoint and notification authority."""

    def __init__(self, store) -> None:
        self.store = store

    def initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS supervisors (
                supervisor_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                role TEXT NOT NULL,
                implementation_allowed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope_type, scope_key)
            );
            CREATE TABLE IF NOT EXISTS durable_agent_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                repository TEXT,
                mission_id TEXT,
                task_id TEXT,
                branch TEXT,
                head_sha TEXT,
                dirty_count INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                summary TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                severity TEXT NOT NULL,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                agent_id TEXT,
                repository TEXT,
                mission_id TEXT,
                task_id TEXT,
                dedupe_key TEXT,
                state TEXT NOT NULL DEFAULT 'OPEN',
                created_at TEXT NOT NULL,
                acknowledged_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_open_dedupe
                ON notifications(dedupe_key) WHERE state='OPEN' AND dedupe_key IS NOT NULL;
            """)

    def register_supervisor(self, supervisor: Supervisor) -> None:
        now = _now().isoformat()
        with self.store.connection() as conn:
            conn.execute("""INSERT INTO supervisors VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(supervisor_id) DO UPDATE SET
                scope_type=excluded.scope_type,scope_key=excluded.scope_key,role=excluded.role,
                implementation_allowed=excluded.implementation_allowed,updated_at=excluded.updated_at""",
                (supervisor.supervisor_id, supervisor.scope_type, supervisor.scope_key,
                 supervisor.role, int(supervisor.implementation_allowed), now, now))

    def checkpoint(self, *, agent_id: str, state: str, summary: str,
                   repository: str | None = None, mission_id: str | None = None,
                   task_id: str | None = None, branch: str | None = None,
                   head_sha: str | None = None, dirty_count: int = 0,
                   evidence: dict | None = None) -> int:
        with self.store.connection() as conn:
            cur = conn.execute("""INSERT INTO durable_agent_checkpoints
                (agent_id,repository,mission_id,task_id,branch,head_sha,dirty_count,state,
                 summary,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (agent_id, repository, mission_id, task_id, branch, head_sha, dirty_count,
                 state, summary, json.dumps(evidence or {}, sort_keys=True), _now().isoformat()))
            return int(cur.lastrowid)

    def notify(self, *, severity: str, kind: str, subject: str, message: str,
               dedupe_key: str | None = None, agent_id: str | None = None,
               repository: str | None = None, mission_id: str | None = None,
               task_id: str | None = None) -> None:
        with self.store.connection() as conn:
            conn.execute("""INSERT OR IGNORE INTO notifications
                (severity,kind,subject,message,agent_id,repository,mission_id,task_id,
                 dedupe_key,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?)""",
                (severity, kind, subject, message, agent_id, repository, mission_id,
                 task_id, dedupe_key, _now().isoformat()))

    def scan_stale_agents(self, agent_registry, *, stale_after_seconds: int = 300) -> list[str]:
        cutoff = _now() - timedelta(seconds=stale_after_seconds)
        stale: list[str] = []
        for lane in agent_registry.lanes():
            heartbeat = lane.get("heartbeat_at")
            state = lane.get("declared_state") or lane.get("state")
            if not heartbeat or state not in {"WORKING", "CLAIMED", "REVIEWING", "TESTING"}:
                continue
            if datetime.fromisoformat(heartbeat) <= cutoff:
                agent_id = lane["agent_id"]
                stale.append(agent_id)
                self.notify(
                    severity="HIGH", kind="AGENT_HEARTBEAT_LOST",
                    subject=f"Agent stopped: {agent_id}",
                    message="Heartbeat expired. Preserve checkpoint and require governed recovery before reassignment.",
                    dedupe_key=f"heartbeat:{agent_id}", agent_id=agent_id,
                    repository=lane.get("repository"), mission_id=lane.get("mission_id"),
                    task_id=lane.get("task_id"),
                )
        return stale

    def notifications(self) -> list[dict]:
        with self.store.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM notifications ORDER BY id DESC").fetchall()]
