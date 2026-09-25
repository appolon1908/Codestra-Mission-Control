
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


# Statuses that assert merge readiness; entering them needs merge-coordinator authorization.
GATED_STATUSES = frozenset({MissionStatus.MERGE_READY, MissionStatus.COMPLETE})


class CompletionBlocked(RuntimeError):
    def __init__(self, mission_id: str, status: MissionStatus, reasons: list[str]) -> None:
        super().__init__(
            f"{mission_id} cannot enter {status.value}: " + ", ".join(reasons)
        )
        self.mission_id = mission_id
        self.status = status
        self.reasons = reasons


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

                CREATE TABLE IF NOT EXISTS head_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    blockers_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mission_dependencies (
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    depends_on TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (mission_id, depends_on)
                );

                CREATE TABLE IF NOT EXISTS merge_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    head_sha TEXT,
                    verdict TEXT NOT NULL,
                    conflict_class INTEGER NOT NULL,
                    next_gate TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    ledger_seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_mission
                    ON events(mission_id, id);
                CREATE INDEX IF NOT EXISTS idx_head_evidence_mission
                    ON head_evidence(mission_id, role, id);
                CREATE INDEX IF NOT EXISTS idx_merge_decisions_mission
                    ON merge_decisions(mission_id, id);
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

    def require_transition(self, mission_id: str, status: MissionStatus) -> None:
        if status not in GATED_STATUSES:
            return
        authorization = self.merge_authorization(mission_id)
        if not authorization["authorized"]:
            raise CompletionBlocked(mission_id, status, authorization["reasons"])

    def set_status(self, mission_id: str, status: MissionStatus) -> None:
        self.require_transition(mission_id, status)
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

    def record_mission_head(self, mission_id: str, head_sha: str, actor: str) -> dict:
        """Bind the mission to a new exact head; any MERGE_READY state is revoked."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            mission = conn.execute(
                "SELECT head_sha, status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not mission:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            previous = mission["head_sha"]
            status = mission["status"]
            changed = previous != head_sha
            if changed and status == MissionStatus.MERGE_READY.value:
                status = MissionStatus.IN_REVIEW.value
            stale = conn.execute(
                """
                SELECT count(*) AS n FROM head_evidence
                WHERE mission_id=? AND head_sha<>?
                """,
                (mission_id, head_sha),
            ).fetchone()["n"]
            conn.execute(
                "UPDATE missions SET head_sha=?, status=?, updated_at=? WHERE mission_id=?",
                (head_sha, status, _iso_now(), mission_id),
            )
            self._event(
                conn,
                mission_id,
                "HEAD_RECORDED",
                actor,
                {"previous_head_sha": previous, "head_sha": head_sha, "changed": changed},
            )
            conn.execute("COMMIT")
        return {
            "mission_id": mission_id,
            "previous_head_sha": previous,
            "head_sha": head_sha,
            "changed": changed,
            "status": status,
            "stale_evidence_count": int(stale),
        }

    def record_head_evidence(
        self,
        mission_id: str,
        *,
        role: str,
        head_sha: str,
        actor: str,
        verdict: str,
        blockers: list[str],
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO head_evidence (
                    mission_id, role, head_sha, actor, verdict, blockers_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    role,
                    head_sha,
                    actor,
                    verdict,
                    json.dumps(blockers),
                    _iso_now(),
                ),
            )
            self._event(
                conn,
                mission_id,
                "HEAD_EVIDENCE_RECORDED",
                actor,
                {"role": role, "head_sha": head_sha, "verdict": verdict, "blockers": blockers},
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def head_evidence(self, mission_id: str, role: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM head_evidence
                    WHERE mission_id=? AND role=?
                    ORDER BY id DESC
                    """,
                    (mission_id, role),
                )
            )

    def writer_agents(self, mission_id: str) -> set[str]:
        """Every agent that ever held the writer lease or recorded a mission head."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT agent_id, event_type, payload_json FROM events
                WHERE mission_id=? AND agent_id IS NOT NULL
                  AND event_type IN ('LEASE_CLAIMED', 'LEASE_TAKEOVER', 'HEAD_RECORDED')
                """,
                (mission_id,),
            ).fetchall()
            current = conn.execute(
                "SELECT agent_id, role FROM leases WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
        writers = set()
        for row in rows:
            if row["event_type"] == "HEAD_RECORDED":
                writers.add(row["agent_id"])
                continue
            role = json.loads(row["payload_json"]).get("role")
            if role in (None, "WRITER"):
                writers.add(row["agent_id"])
        if current and current["role"] == "WRITER":
            writers.add(current["agent_id"])
        return writers

    def add_dependency(self, mission_id: str, depends_on: str) -> None:
        if mission_id == depends_on:
            raise ValueError("mission cannot depend on itself")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone():
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            # Reject edges that would close a cycle: depends_on must not reach mission_id.
            frontier = [depends_on]
            seen: set[str] = set()
            while frontier:
                node = frontier.pop()
                if node == mission_id:
                    conn.execute("ROLLBACK")
                    raise ValueError(f"dependency cycle: {mission_id} -> {depends_on}")
                if node in seen:
                    continue
                seen.add(node)
                frontier.extend(
                    row["depends_on"]
                    for row in conn.execute(
                        "SELECT depends_on FROM mission_dependencies WHERE mission_id=?",
                        (node,),
                    )
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO mission_dependencies (mission_id, depends_on, created_at)
                VALUES (?, ?, ?)
                """,
                (mission_id, depends_on, _iso_now()),
            )
            self._event(conn, mission_id, "DEPENDENCY_ADDED", None, {"depends_on": depends_on})
            conn.execute("COMMIT")

    def dependencies(self, mission_id: str) -> list[str]:
        with self.connection() as conn:
            return [
                row["depends_on"]
                for row in conn.execute(
                    """
                    SELECT depends_on FROM mission_dependencies
                    WHERE mission_id=? ORDER BY depends_on
                    """,
                    (mission_id,),
                )
            ]

    def record_merge_decision(
        self,
        mission_id: str,
        *,
        head_sha: str | None,
        verdict: str,
        conflict_class: int,
        next_gate: str,
        reasons: list[str],
        snapshot: dict,
        next_status: MissionStatus | None,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            seq_row = conn.execute(
                "SELECT coalesce(max(id), 0) AS seq FROM events WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO merge_decisions (
                    mission_id, head_sha, verdict, conflict_class, next_gate,
                    reasons_json, snapshot_json, ledger_seq, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    head_sha,
                    verdict,
                    conflict_class,
                    next_gate,
                    json.dumps(reasons),
                    json.dumps(snapshot, sort_keys=True),
                    int(seq_row["seq"]),
                    _iso_now(),
                ),
            )
            if next_status is not None:
                conn.execute(
                    "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                    (next_status.value, _iso_now(), mission_id),
                )
            self._event(
                conn,
                mission_id,
                "MERGE_DECISION_RECORDED",
                None,
                {
                    "decision_id": int(cursor.lastrowid),
                    "head_sha": head_sha,
                    "verdict": verdict,
                    "conflict_class": conflict_class,
                    "next_gate": next_gate,
                    "reasons": reasons,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def latest_merge_decision(self, mission_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM merge_decisions
                WHERE mission_id=? ORDER BY id DESC LIMIT 1
                """,
                (mission_id,),
            ).fetchone()

    def merge_authorization(self, mission_id: str) -> dict:
        """Fail-closed: authorized only by a current MERGE_READY decision on the exact head."""
        mission = self.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        head = mission["head_sha"]
        decision = self.latest_merge_decision(mission_id)
        reasons: list[str] = []
        if not head:
            reasons.append("NO_MISSION_HEAD")
        if decision is None:
            reasons.append("NO_MERGE_DECISION")
        else:
            if decision["verdict"] != "MERGE_READY":
                reasons.append("MERGE_DECISION_BLOCKED")
            if decision["head_sha"] != head:
                reasons.append("MERGE_DECISION_STALE_HEAD")
            with self.connection() as conn:
                superseding = conn.execute(
                    """
                    SELECT count(*) AS n FROM events
                    WHERE mission_id=? AND id>?
                      AND event_type IN (
                        'HEAD_RECORDED', 'HEAD_EVIDENCE_RECORDED', 'DEPENDENCY_ADDED'
                      )
                    """,
                    (mission_id, decision["ledger_seq"]),
                ).fetchone()["n"]
            if superseding:
                reasons.append("MERGE_DECISION_SUPERSEDED")
        return {
            "mission_id": mission_id,
            "authorized": not reasons,
            "head_sha": head,
            "decision_id": decision["id"] if decision else None,
            "reasons": reasons,
        }

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
