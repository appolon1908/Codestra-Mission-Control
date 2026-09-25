
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import ApprovalLevel, Mission, MissionStatus


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class MissionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    branch TEXT,
                    worktree TEXT,
                    base_sha TEXT,
                    head_sha TEXT,
                    required_approval INTEGER NOT NULL,
                    acceptance_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repositories (
                    repository TEXT PRIMARY KEY,
                    full_name TEXT,
                    local_path TEXT,
                    origin_url TEXT,
                    default_branch TEXT,
                    visibility TEXT,
                    local_present INTEGER NOT NULL DEFAULT 0,
                    mission_channel_path TEXT,
                    workspace_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS leases (
                    mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_executions (
                    execution_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    state TEXT NOT NULL,
                    runner_pid INTEGER,
                    worktree TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    stdout_path TEXT NOT NULL,
                    stderr_path TEXT NOT NULL,
                    result_path TEXT NOT NULL,
                    session_id TEXT,
                    exit_code INTEGER,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    head_sha TEXT,
                    dirty_count INTEGER,
                    tests_json TEXT NOT NULL,
                    blockers_json TEXT NOT NULL,
                    next_task_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    level INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    agent_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watchdog_escalations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    agent_id TEXT,
                    level INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    delivery TEXT NOT NULL DEFAULT 'LOCAL_ONLY',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    acknowledged_by TEXT,
                    acknowledged_at TEXT,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS watchdog_dispatches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    state TEXT NOT NULL,
                    execution_id TEXT,
                    takeover INTEGER NOT NULL DEFAULT 0,
                    predecessor_agent_id TEXT,
                    predecessor_execution_id TEXT,
                    checkpoint_head TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_mission
                    ON events(mission_id, id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_watchdog_escalations_active
                    ON watchdog_escalations(mission_id, kind)
                    WHERE state != 'RESOLVED';
                CREATE INDEX IF NOT EXISTS idx_watchdog_dispatches_mission
                    ON watchdog_dispatches(mission_id, id);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_mission
                    ON checkpoints(mission_id, id);
                """
            )

    def upsert_mission(self, mission: Mission) -> None:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO missions (
                    mission_id, repository, goal, status, branch, worktree,
                    base_sha, head_sha, required_approval, acceptance_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    repository=excluded.repository,
                    goal=excluded.goal,
                    status=excluded.status,
                    branch=excluded.branch,
                    worktree=excluded.worktree,
                    base_sha=excluded.base_sha,
                    head_sha=excluded.head_sha,
                    required_approval=excluded.required_approval,
                    acceptance_json=excluded.acceptance_json,
                    updated_at=excluded.updated_at
                """,
                (
                    mission.mission_id,
                    mission.repository,
                    mission.goal,
                    mission.status.value,
                    mission.branch,
                    mission.worktree,
                    mission.base_sha,
                    mission.head_sha,
                    int(mission.required_approval),
                    json.dumps(mission.acceptance),
                    now,
                    now,
                ),
            )
            self._event(
                conn,
                mission.mission_id,
                "MISSION_UPSERTED",
                None,
                {"status": mission.status.value, "repository": mission.repository},
            )
            conn.execute("COMMIT")

    def upsert_repository(
        self,
        repository: str,
        *,
        full_name: str | None,
        local_path: str | None,
        origin_url: str | None,
        default_branch: str | None,
        visibility: str | None,
        local_present: bool,
        mission_channel_path: str | None,
        workspace_path: str | None,
    ) -> None:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO repositories (
                    repository, full_name, local_path, origin_url, default_branch,
                    visibility, local_present, mission_channel_path, workspace_path,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository) DO UPDATE SET
                    full_name=excluded.full_name,
                    local_path=excluded.local_path,
                    origin_url=excluded.origin_url,
                    default_branch=excluded.default_branch,
                    visibility=excluded.visibility,
                    local_present=excluded.local_present,
                    mission_channel_path=excluded.mission_channel_path,
                    workspace_path=excluded.workspace_path,
                    updated_at=excluded.updated_at
                """,
                (
                    repository,
                    full_name,
                    local_path,
                    origin_url,
                    default_branch,
                    visibility,
                    int(local_present),
                    mission_channel_path,
                    workspace_path,
                    now,
                    now,
                ),
            )

    def list_repositories(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM repositories ORDER BY lower(repository), repository"
                )
            )

    def list_missions(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM missions ORDER BY updated_at, mission_id"
                )
            )

    def get_repository(self, repository: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM repositories WHERE repository=?",
                (repository,),
            ).fetchone()

    def update_mission_workspace(
        self,
        mission_id: str,
        *,
        branch: str,
        worktree: str,
        base_sha: str,
        head_sha: str,
    ) -> None:
        with self.connection() as conn:
            changed = conn.execute(
                """
                UPDATE missions
                SET branch=?, worktree=?, base_sha=?, head_sha=?, updated_at=?
                WHERE mission_id=?
                """,
                (branch, worktree, base_sha, head_sha, _iso_now(), mission_id),
            ).rowcount
            if not changed:
                raise KeyError(mission_id)

    def list_agent_executions(
        self,
        *,
        states: tuple[str, ...] | None = None,
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            if not states:
                return list(
                    conn.execute(
                        "SELECT * FROM agent_executions ORDER BY started_at"
                    )
                )
            placeholders = ",".join("?" for _ in states)
            return list(
                conn.execute(
                    f"""
                    SELECT * FROM agent_executions
                    WHERE state IN ({placeholders})
                    ORDER BY started_at
                    """,
                    states,
                )
            )

    def latest_agent_execution(self, mission_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM agent_executions
                WHERE mission_id=?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (mission_id,),
            ).fetchone()

    def latest_checkpoint(self, mission_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE mission_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (mission_id,),
            ).fetchone()

    def get_mission(self, mission_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()

    def set_status(self, mission_id: str, status: MissionStatus) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                (status.value, _iso_now(), mission_id),
            ).rowcount
            if not changed:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            self._event(conn, mission_id, "STATUS_CHANGED", None, {"status": status.value})
            conn.execute("COMMIT")

    def create_agent_execution(
        self,
        *,
        execution_id: str,
        mission_id: str,
        agent_id: str,
        provider: str,
        state: str,
        runner_pid: int,
        worktree: str,
        command: list[str],
        stdout_path: str,
        stderr_path: str,
        result_path: str,
    ) -> None:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_executions (
                    execution_id, mission_id, agent_id, provider, state,
                    runner_pid, worktree, command_json, stdout_path, stderr_path,
                    result_path, started_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    mission_id,
                    agent_id,
                    provider,
                    state,
                    runner_pid,
                    worktree,
                    json.dumps(command),
                    stdout_path,
                    stderr_path,
                    result_path,
                    now,
                    now,
                ),
            )
            self._event(
                conn,
                mission_id,
                "AGENT_EXECUTION_STARTED",
                agent_id,
                {
                    "execution_id": execution_id,
                    "provider": provider,
                    "runner_pid": runner_pid,
                    "worktree": worktree,
                },
            )

    def update_agent_execution(
        self,
        execution_id: str,
        *,
        state: str | None = None,
        session_id: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[object] = []
        if state is not None:
            fields.append("state=?")
            values.append(state)
        if session_id is not None:
            fields.append("session_id=?")
            values.append(session_id)
        if exit_code is not None:
            fields.append("exit_code=?")
            values.append(exit_code)
        if not fields:
            return
        fields.append("updated_at=?")
        values.append(_iso_now())
        values.append(execution_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE agent_executions SET {', '.join(fields)} WHERE execution_id=?",
                values,
            )

    def get_agent_execution(self, execution_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM agent_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()

    def record_checkpoint(
        self,
        mission_id: str,
        agent_id: str,
        state: str,
        *,
        head_sha: str | None,
        dirty_count: int | None,
        tests: dict,
        blockers: list[str],
        next_task_requested: bool,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO checkpoints (
                    mission_id, agent_id, state, head_sha, dirty_count,
                    tests_json, blockers_json, next_task_requested, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    agent_id,
                    state,
                    head_sha,
                    dirty_count,
                    json.dumps(tests, sort_keys=True),
                    json.dumps(blockers),
                    int(next_task_requested),
                    _iso_now(),
                ),
            )
            self._event(
                conn,
                mission_id,
                "CHECKPOINT_RECORDED",
                agent_id,
                {
                    "state": state,
                    "head_sha": head_sha,
                    "dirty_count": dirty_count,
                    "blockers": blockers,
                    "next_task_requested": next_task_requested,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def record_approval(
        self,
        mission_id: str,
        level: ApprovalLevel,
        actor: str,
        status: str = "APPROVED",
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO approvals (mission_id, level, actor, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mission_id, int(level), actor, status, _iso_now()),
            )
            self._event(
                conn,
                mission_id,
                "APPROVAL_RECORDED",
                actor,
                {"level": int(level), "status": status},
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def highest_approval(self, mission_id: str) -> ApprovalLevel:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT max(level) AS level
                FROM approvals
                WHERE mission_id=? AND status='APPROVED'
                """,
                (mission_id,),
            ).fetchone()
            return ApprovalLevel(int(row["level"] or 0))

    def list_leases(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(conn.execute("SELECT * FROM leases ORDER BY mission_id"))

    def latest_agent_execution_for(
        self,
        mission_id: str,
        agent_id: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM agent_executions
                WHERE mission_id=? AND agent_id=?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (mission_id, agent_id),
            ).fetchone()

    def record_watchdog_dispatch(
        self,
        *,
        mission_id: str,
        agent_id: str,
        provider: str,
        state: str,
        execution_id: str | None,
        takeover: bool,
        predecessor_agent_id: str | None,
        predecessor_execution_id: str | None,
        checkpoint_head: str | None,
        reason: str | None,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO watchdog_dispatches (
                    mission_id, agent_id, provider, state, execution_id, takeover,
                    predecessor_agent_id, predecessor_execution_id, checkpoint_head,
                    reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    agent_id,
                    provider,
                    state,
                    execution_id,
                    int(takeover),
                    predecessor_agent_id,
                    predecessor_execution_id,
                    checkpoint_head,
                    reason,
                    _iso_now(),
                ),
            )
            self._event(
                conn,
                mission_id,
                "SUCCESSOR_DISPATCHED" if takeover else "WATCHDOG_DISPATCHED",
                agent_id,
                {
                    "dispatch_id": int(cursor.lastrowid),
                    "state": state,
                    "execution_id": execution_id,
                    "predecessor_agent_id": predecessor_agent_id,
                    "predecessor_execution_id": predecessor_execution_id,
                    "checkpoint_head": checkpoint_head,
                    "reason": reason,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def list_watchdog_dispatches(
        self,
        *,
        mission_id: str | None = None,
        takeover_only: bool = False,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        values: list[object] = []
        if mission_id:
            clauses.append("mission_id=?")
            values.append(mission_id)
        if takeover_only:
            clauses.append("takeover=1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 1000)))
        with self.connection() as conn:
            return list(
                conn.execute(
                    f"SELECT * FROM watchdog_dispatches {where} ORDER BY id DESC LIMIT ?",
                    values,
                )
            )

    def latest_watchdog_dispatches(self) -> dict[str, sqlite3.Row]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT d.* FROM watchdog_dispatches d
                JOIN (
                    SELECT mission_id, max(id) AS id
                    FROM watchdog_dispatches
                    GROUP BY mission_id
                ) latest ON latest.id = d.id
                """
            ).fetchall()
            return {row["mission_id"]: row for row in rows}

    def list_watchdog_escalations(
        self,
        *,
        states: tuple[str, ...] | None = None,
        mission_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        values: list[object] = []
        if states:
            clauses.append(f"state IN ({','.join('?' for _ in states)})")
            values.extend(states)
        if mission_id:
            clauses.append("mission_id=?")
            values.append(mission_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT * FROM watchdog_escalations {where}
                    ORDER BY level DESC, first_seen_at, id
                    """,
                    values,
                )
            )

    def get_watchdog_escalation(self, escalation_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM watchdog_escalations WHERE id=?",
                (escalation_id,),
            ).fetchone()

    def sync_watchdog_escalations(
        self,
        conditions: list[dict],
        *,
        observed_at: str,
    ) -> dict[str, list[int]]:
        """Reconcile active escalations with the currently observed conditions.

        Each condition is a dict with mission_id, kind, agent_id, level and detail.
        New conditions open escalations, higher levels re-open acknowledged ones,
        and active escalations with no matching condition are resolved.
        """
        opened: list[int] = []
        raised: list[int] = []
        resolved: list[int] = []
        wanted = {(item["mission_id"], item["kind"]): item for item in conditions}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = {
                (row["mission_id"], row["kind"]): row
                for row in conn.execute(
                    "SELECT * FROM watchdog_escalations WHERE state != 'RESOLVED'"
                )
            }
            for key, item in wanted.items():
                detail = json.dumps(item.get("detail") or {}, sort_keys=True)
                row = active.get(key)
                if row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO watchdog_escalations (
                            mission_id, kind, agent_id, level, state, detail_json,
                            first_seen_at, last_seen_at
                        )
                        VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?)
                        """,
                        (
                            item["mission_id"],
                            item["kind"],
                            item.get("agent_id"),
                            int(item["level"]),
                            detail,
                            observed_at,
                            observed_at,
                        ),
                    )
                    escalation_id = int(cursor.lastrowid)
                    opened.append(escalation_id)
                    self._event(
                        conn,
                        item["mission_id"],
                        "ESCALATION_OPENED",
                        item.get("agent_id"),
                        {
                            "escalation_id": escalation_id,
                            "kind": item["kind"],
                            "level": int(item["level"]),
                        },
                    )
                    continue
                level = int(item["level"])
                state = row["state"]
                if level > int(row["level"]):
                    state = "OPEN"
                    raised.append(int(row["id"]))
                    self._event(
                        conn,
                        item["mission_id"],
                        "ESCALATION_RAISED",
                        item.get("agent_id"),
                        {
                            "escalation_id": int(row["id"]),
                            "kind": item["kind"],
                            "from_level": int(row["level"]),
                            "level": level,
                        },
                    )
                conn.execute(
                    """
                    UPDATE watchdog_escalations
                    SET level=max(level, ?), state=?, agent_id=?, detail_json=?,
                        last_seen_at=?
                    WHERE id=?
                    """,
                    (level, state, item.get("agent_id"), detail, observed_at, row["id"]),
                )
            for key, row in active.items():
                if key in wanted:
                    continue
                conn.execute(
                    """
                    UPDATE watchdog_escalations
                    SET state='RESOLVED', resolved_at=?
                    WHERE id=?
                    """,
                    (observed_at, row["id"]),
                )
                resolved.append(int(row["id"]))
                self._event(
                    conn,
                    row["mission_id"],
                    "ESCALATION_RESOLVED",
                    row["agent_id"],
                    {"escalation_id": int(row["id"]), "kind": row["kind"]},
                )
            conn.execute("COMMIT")
        return {"opened": opened, "raised": raised, "resolved": resolved}

    def acknowledge_watchdog_escalation(self, escalation_id: int, actor: str) -> sqlite3.Row:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM watchdog_escalations WHERE id=?",
                (escalation_id,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(escalation_id)
            if row["state"] == "RESOLVED":
                conn.execute("ROLLBACK")
                raise ValueError(f"escalation {escalation_id} is already resolved")
            now = _iso_now()
            conn.execute(
                """
                UPDATE watchdog_escalations
                SET state='ACKNOWLEDGED', acknowledged_by=?, acknowledged_at=?
                WHERE id=?
                """,
                (actor, now, escalation_id),
            )
            self._event(
                conn,
                row["mission_id"],
                "ESCALATION_ACKNOWLEDGED",
                actor,
                {"escalation_id": escalation_id, "kind": row["kind"]},
            )
            updated = conn.execute(
                "SELECT * FROM watchdog_escalations WHERE id=?",
                (escalation_id,),
            ).fetchone()
            conn.execute("COMMIT")
            return updated

    def events(self, mission_id: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM events WHERE mission_id=? ORDER BY id",
                    (mission_id,),
                )
            )

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        mission_id: str,
        event_type: str,
        agent_id: str | None,
        payload: dict,
    ) -> None:
        conn.execute(
            """
            INSERT INTO events (mission_id, event_type, agent_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                event_type,
                agent_id,
                json.dumps(payload, sort_keys=True),
                _iso_now(),
            ),
        )
