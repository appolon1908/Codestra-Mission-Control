
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import ApprovalLevel, Mission, MissionStatus


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

                CREATE TABLE IF NOT EXISTS leases (
                    mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
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

                CREATE INDEX IF NOT EXISTS idx_events_mission
                    ON events(mission_id, id);
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
