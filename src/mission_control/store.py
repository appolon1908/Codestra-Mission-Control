
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    AgentRole,
    ApprovalGate,
    ApprovalLevel,
    ApprovalStatus,
    ConflictClass,
    ConflictStatus,
    DispatchState,
    MergeQueueState,
    Mission,
    MissionStatus,
    NotificationChannel,
    NotificationIncidentState,
    NotificationOutboxState,
)


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

                CREATE TABLE IF NOT EXISTS sha_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    gate TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conflict_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    repository TEXT NOT NULL,
                    pr_number INTEGER,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT,
                    conflict_class TEXT NOT NULL,
                    files_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution_mission_id TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS merge_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL UNIQUE
                        REFERENCES missions(mission_id) ON DELETE CASCADE,
                    repository TEXT NOT NULL,
                    pr_number INTEGER,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT,
                    target_sha TEXT,
                    priority INTEGER NOT NULL DEFAULT 50,
                    state TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS merge_dependencies (
                    queue_id INTEGER NOT NULL REFERENCES merge_queue(id) ON DELETE CASCADE,
                    depends_on_mission_id TEXT NOT NULL,
                    required_merge_sha TEXT,
                    state TEXT NOT NULL DEFAULT 'WAITING',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(queue_id, depends_on_mission_id)
                );

                CREATE TABLE IF NOT EXISTS merge_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queue_id INTEGER NOT NULL REFERENCES merge_queue(id) ON DELETE CASCADE,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    repository TEXT NOT NULL,
                    pr_number INTEGER,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT,
                    merge_sha TEXT NOT NULL,
                    merge_method TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dispatch_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    head_sha TEXT,
                    state TEXT NOT NULL,
                    execution_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_key TEXT NOT NULL UNIQUE,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    required_role TEXT NOT NULL,
                    head_sha TEXT,
                    reason TEXT NOT NULL,
                    link TEXT,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_notification_at TEXT,
                    email_escalated_at TEXT,
                    acknowledged_at TEXT,
                    resolved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL
                        REFERENCES notification_incidents(id) ON DELETE CASCADE,
                    channel TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    sent_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(incident_id, channel, attempt_no)
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
                CREATE INDEX IF NOT EXISTS idx_sha_approvals_mission_head
                    ON sha_approvals(mission_id, head_sha, gate, status);
                CREATE INDEX IF NOT EXISTS idx_conflicts_mission
                    ON conflict_records(mission_id, status, id);
                CREATE INDEX IF NOT EXISTS idx_merge_queue_state
                    ON merge_queue(state, priority, id);
                CREATE INDEX IF NOT EXISTS idx_dispatch_requests_state
                    ON dispatch_requests(state, role, id);
                CREATE INDEX IF NOT EXISTS idx_notification_incidents_due
                    ON notification_incidents(state, next_notification_at, id);
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_due
                    ON notification_outbox(state, available_at, id);
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
            if head_sha:
                mission = conn.execute(
                    "SELECT head_sha FROM missions WHERE mission_id=?",
                    (mission_id,),
                ).fetchone()
                if not mission:
                    conn.execute("ROLLBACK")
                    raise KeyError(mission_id)
                previous_head = mission["head_sha"]
                if previous_head != head_sha:
                    conn.execute(
                        "UPDATE missions SET head_sha=?, updated_at=? WHERE mission_id=?",
                        (head_sha, _iso_now(), mission_id),
                    )
                    invalidated = conn.execute(
                        """
                        UPDATE sha_approvals
                        SET status=?, invalidated_at=?, invalidation_reason=?
                        WHERE mission_id=? AND status=? AND head_sha<>?
                        """,
                        (
                            ApprovalStatus.STALE.value,
                            _iso_now(),
                            f"mission head moved to {head_sha}",
                            mission_id,
                            ApprovalStatus.APPROVED.value,
                            head_sha,
                        ),
                    ).rowcount
                    if invalidated:
                        self._event(
                            conn,
                            mission_id,
                            "SHA_APPROVALS_INVALIDATED",
                            agent_id,
                            {
                                "previous_head_sha": previous_head,
                                "current_head_sha": head_sha,
                                "count": invalidated,
                            },
                        )
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

    def observe_head(
        self,
        mission_id: str,
        *,
        head_sha: str,
        base_sha: str | None = None,
        actor: str = "merge-coordinator",
    ) -> int:
        """Persist the observed PR head and invalidate approvals for older heads."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            mission = conn.execute(
                "SELECT head_sha, base_sha FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not mission:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            previous_head = mission["head_sha"]
            previous_base = mission["base_sha"]
            conn.execute(
                """
                UPDATE missions
                SET head_sha=?, base_sha=COALESCE(?, base_sha), updated_at=?
                WHERE mission_id=?
                """,
                (head_sha, base_sha, _iso_now(), mission_id),
            )
            invalidated = conn.execute(
                """
                UPDATE sha_approvals
                SET status=?, invalidated_at=?, invalidation_reason=?
                WHERE mission_id=? AND status=? AND head_sha<>?
                """,
                (
                    ApprovalStatus.STALE.value,
                    _iso_now(),
                    f"head moved to {head_sha}",
                    mission_id,
                    ApprovalStatus.APPROVED.value,
                    head_sha,
                ),
            ).rowcount
            self._event(
                conn,
                mission_id,
                "HEAD_OBSERVED",
                actor,
                {
                    "previous_head_sha": previous_head,
                    "current_head_sha": head_sha,
                    "previous_base_sha": previous_base,
                    "current_base_sha": base_sha or previous_base,
                    "invalidated_approvals": invalidated,
                },
            )
            conn.execute("COMMIT")
            return int(invalidated)

    def record_sha_approval(
        self,
        mission_id: str,
        *,
        gate: ApprovalGate,
        actor: str,
        actor_role: AgentRole,
        head_sha: str,
        base_sha: str | None = None,
        status: ApprovalStatus = ApprovalStatus.APPROVED,
        evidence: dict | None = None,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            mission = conn.execute(
                "SELECT head_sha FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not mission:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            current_head = mission["head_sha"]
            if current_head and current_head != head_sha and status is ApprovalStatus.APPROVED:
                conn.execute("ROLLBACK")
                raise ValueError(
                    f"approval head {head_sha} does not match current mission head {current_head}"
                )
            cursor = conn.execute(
                """
                INSERT INTO sha_approvals (
                    mission_id, gate, actor, actor_role, head_sha, base_sha,
                    status, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    gate.value,
                    actor,
                    actor_role.value,
                    head_sha,
                    base_sha,
                    status.value,
                    json.dumps(evidence or {}, sort_keys=True),
                    _iso_now(),
                ),
            )
            self._event(
                conn,
                mission_id,
                "SHA_APPROVAL_RECORDED",
                actor,
                {
                    "approval_id": int(cursor.lastrowid),
                    "gate": gate.value,
                    "actor_role": actor_role.value,
                    "head_sha": head_sha,
                    "base_sha": base_sha,
                    "status": status.value,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def valid_sha_approvals(self, mission_id: str, head_sha: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM sha_approvals
                    WHERE mission_id=? AND head_sha=? AND status=?
                    ORDER BY id
                    """,
                    (mission_id, head_sha, ApprovalStatus.APPROVED.value),
                )
            )

    def latest_valid_sha_approval(
        self,
        mission_id: str,
        gate: ApprovalGate,
        head_sha: str,
    ) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM sha_approvals
                WHERE mission_id=? AND gate=? AND head_sha=? AND status=?
                ORDER BY id DESC LIMIT 1
                """,
                (
                    mission_id,
                    gate.value,
                    head_sha,
                    ApprovalStatus.APPROVED.value,
                ),
            ).fetchone()

    def invalidate_sha_approvals(
        self,
        mission_id: str,
        *,
        current_head_sha: str,
        reason: str,
        actor: str = "merge-coordinator",
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE sha_approvals
                SET status=?, invalidated_at=?, invalidation_reason=?
                WHERE mission_id=? AND status=? AND head_sha<>?
                """,
                (
                    ApprovalStatus.STALE.value,
                    _iso_now(),
                    reason,
                    mission_id,
                    ApprovalStatus.APPROVED.value,
                    current_head_sha,
                ),
            ).rowcount
            if changed:
                self._event(
                    conn,
                    mission_id,
                    "SHA_APPROVALS_INVALIDATED",
                    actor,
                    {
                        "current_head_sha": current_head_sha,
                        "count": int(changed),
                        "reason": reason,
                    },
                )
            conn.execute("COMMIT")
            return int(changed)

    def record_conflict(
        self,
        mission_id: str,
        *,
        repository: str,
        pr_number: int | None,
        head_sha: str,
        base_sha: str | None,
        conflict_class: ConflictClass,
        files: list[str],
        summary: str,
        resolution_mission_id: str | None = None,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO conflict_records (
                    mission_id, repository, pr_number, head_sha, base_sha,
                    conflict_class, files_json, summary, status,
                    resolution_mission_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    repository,
                    pr_number,
                    head_sha,
                    base_sha,
                    conflict_class.value,
                    json.dumps(files),
                    summary,
                    ConflictStatus.OPEN.value,
                    resolution_mission_id,
                    _iso_now(),
                ),
            )
            self._event(
                conn,
                mission_id,
                "CONFLICT_RECORDED",
                None,
                {
                    "conflict_id": int(cursor.lastrowid),
                    "class": conflict_class.value,
                    "head_sha": head_sha,
                    "files": files,
                    "summary": summary,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def latest_open_conflict(self, mission_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM conflict_records
                WHERE mission_id=? AND status=?
                ORDER BY id DESC LIMIT 1
                """,
                (mission_id, ConflictStatus.OPEN.value),
            ).fetchone()

    def resolve_conflict(
        self,
        conflict_id: int,
        *,
        actor: str,
        resolution_mission_id: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT mission_id FROM conflict_records WHERE id=?",
                (conflict_id,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(conflict_id)
            conn.execute(
                """
                UPDATE conflict_records
                SET status=?, resolved_at=?,
                    resolution_mission_id=COALESCE(?, resolution_mission_id)
                WHERE id=?
                """,
                (
                    ConflictStatus.RESOLVED.value,
                    _iso_now(),
                    resolution_mission_id,
                    conflict_id,
                ),
            )
            self._event(
                conn,
                row["mission_id"],
                "CONFLICT_RESOLVED",
                actor,
                {"conflict_id": conflict_id, "resolution_mission_id": resolution_mission_id},
            )
            conn.execute("COMMIT")

    def enqueue_merge(
        self,
        mission_id: str,
        *,
        repository: str,
        pr_number: int | None,
        head_sha: str,
        base_sha: str | None,
        target_sha: str | None,
        priority: int = 50,
        state: MergeQueueState = MergeQueueState.QUEUED,
        reason: str | None = None,
    ) -> int:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO merge_queue (
                    mission_id, repository, pr_number, head_sha, base_sha, target_sha,
                    priority, state, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    repository=excluded.repository,
                    pr_number=excluded.pr_number,
                    head_sha=excluded.head_sha,
                    base_sha=excluded.base_sha,
                    target_sha=excluded.target_sha,
                    priority=excluded.priority,
                    state=excluded.state,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (
                    mission_id,
                    repository,
                    pr_number,
                    head_sha,
                    base_sha,
                    target_sha,
                    int(priority),
                    state.value,
                    reason,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM merge_queue WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            assert row is not None
            queue_id = int(row["id"])
            self._event(
                conn,
                mission_id,
                "MERGE_QUEUED",
                None,
                {
                    "queue_id": queue_id,
                    "repository": repository,
                    "pr_number": pr_number,
                    "head_sha": head_sha,
                    "base_sha": base_sha,
                    "target_sha": target_sha,
                    "priority": priority,
                    "state": state.value,
                },
            )
            conn.execute("COMMIT")
            return queue_id

    def get_merge_queue_item(self, mission_id: str) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM merge_queue WHERE mission_id=?",
                (mission_id,),
            ).fetchone()

    def list_merge_queue(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM merge_queue ORDER BY priority, id"
                )
            )

    def set_merge_queue_state(
        self,
        mission_id: str,
        state: MergeQueueState,
        *,
        reason: str | None = None,
        actor: str = "merge-coordinator",
    ) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE merge_queue SET state=?, reason=?, updated_at=?
                WHERE mission_id=?
                """,
                (state.value, reason, _iso_now(), mission_id),
            ).rowcount
            if not changed:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            self._event(
                conn,
                mission_id,
                "MERGE_QUEUE_STATE",
                actor,
                {"state": state.value, "reason": reason},
            )
            conn.execute("COMMIT")

    def add_merge_dependency(
        self,
        mission_id: str,
        depends_on_mission_id: str,
        *,
        required_merge_sha: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            queue = conn.execute(
                "SELECT id FROM merge_queue WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not queue:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            conn.execute(
                """
                INSERT INTO merge_dependencies (
                    queue_id, depends_on_mission_id, required_merge_sha, state, updated_at
                ) VALUES (?, ?, ?, 'WAITING', ?)
                ON CONFLICT(queue_id, depends_on_mission_id) DO UPDATE SET
                    required_merge_sha=excluded.required_merge_sha,
                    updated_at=excluded.updated_at
                """,
                (
                    int(queue["id"]),
                    depends_on_mission_id,
                    required_merge_sha,
                    _iso_now(),
                ),
            )
            conn.execute("COMMIT")

    def merge_dependencies_satisfied(self, mission_id: str) -> bool:
        with self.connection() as conn:
            queue = conn.execute(
                "SELECT id FROM merge_queue WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not queue:
                return True
            rows = conn.execute(
                """
                SELECT d.depends_on_mission_id, d.required_merge_sha,
                       r.merge_sha AS actual_merge_sha
                FROM merge_dependencies d
                LEFT JOIN merge_results r
                  ON r.mission_id=d.depends_on_mission_id
                 AND r.id=(SELECT max(r2.id) FROM merge_results r2
                           WHERE r2.mission_id=d.depends_on_mission_id)
                WHERE d.queue_id=?
                """,
                (int(queue["id"]),),
            ).fetchall()
            for row in rows:
                actual = row["actual_merge_sha"]
                required = row["required_merge_sha"]
                if not actual:
                    return False
                if required and required != actual:
                    return False
            return True

    def record_merge_result(
        self,
        mission_id: str,
        *,
        merge_sha: str,
        merge_method: str,
        actor: str,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            queue = conn.execute(
                "SELECT * FROM merge_queue WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not queue:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)
            cursor = conn.execute(
                """
                INSERT INTO merge_results (
                    queue_id, mission_id, repository, pr_number, head_sha,
                    base_sha, merge_sha, merge_method, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(queue["id"]),
                    mission_id,
                    queue["repository"],
                    queue["pr_number"],
                    queue["head_sha"],
                    queue["base_sha"],
                    merge_sha,
                    merge_method,
                    actor,
                    _iso_now(),
                ),
            )
            conn.execute(
                "UPDATE merge_queue SET state=?, reason=NULL, updated_at=? WHERE id=?",
                (MergeQueueState.MERGED.value, _iso_now(), int(queue["id"])),
            )
            conn.execute(
                "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                (MissionStatus.MERGED.value, _iso_now(), mission_id),
            )
            self._event(
                conn,
                mission_id,
                "MERGE_RECORDED",
                actor,
                {
                    "merge_result_id": int(cursor.lastrowid),
                    "merge_sha": merge_sha,
                    "head_sha": queue["head_sha"],
                    "merge_method": merge_method,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def request_dispatch(
        self,
        mission_id: str,
        *,
        role: AgentRole,
        reason: str,
        head_sha: str | None,
    ) -> int:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT id FROM dispatch_requests
                WHERE mission_id=? AND role=? AND head_sha IS ? AND state IN (?, ?)
                ORDER BY id DESC LIMIT 1
                """,
                (
                    mission_id,
                    role.value,
                    head_sha,
                    DispatchState.PENDING.value,
                    DispatchState.RUNNING.value,
                ),
            ).fetchone()
            if existing:
                conn.execute("COMMIT")
                return int(existing["id"])
            cursor = conn.execute(
                """
                INSERT INTO dispatch_requests (
                    mission_id, role, reason, head_sha, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    role.value,
                    reason,
                    head_sha,
                    DispatchState.PENDING.value,
                    now,
                    now,
                ),
            )
            self._event(
                conn,
                mission_id,
                "REDISPATCH_REQUESTED",
                None,
                {
                    "dispatch_id": int(cursor.lastrowid),
                    "role": role.value,
                    "reason": reason,
                    "head_sha": head_sha,
                },
            )
            conn.execute("COMMIT")
            return int(cursor.lastrowid)

    def pending_dispatch_requests(
        self,
        *,
        role: AgentRole | None = None,
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            if role is None:
                return list(
                    conn.execute(
                        """
                        SELECT * FROM dispatch_requests
                        WHERE state=? ORDER BY id
                        """,
                        (DispatchState.PENDING.value,),
                    )
                )
            return list(
                conn.execute(
                    """
                    SELECT * FROM dispatch_requests
                    WHERE state=? AND role=? ORDER BY id
                    """,
                    (DispatchState.PENDING.value, role.value),
                )
            )

    def list_dispatch_requests(
        self,
        *,
        states: tuple[DispatchState, ...] | None = None,
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            if not states:
                return list(conn.execute("SELECT * FROM dispatch_requests ORDER BY id"))
            values = tuple(state.value for state in states)
            placeholders = ",".join("?" for _ in values)
            return list(
                conn.execute(
                    f"SELECT * FROM dispatch_requests WHERE state IN ({placeholders}) ORDER BY id",
                    values,
                )
            )

    def update_dispatch_request(
        self,
        dispatch_id: int,
        *,
        state: DispatchState,
        execution_id: str | None = None,
    ) -> None:
        with self.connection() as conn:
            changed = conn.execute(
                """
                UPDATE dispatch_requests
                SET state=?, execution_id=COALESCE(?, execution_id), updated_at=?
                WHERE id=?
                """,
                (state.value, execution_id, _iso_now(), dispatch_id),
            ).rowcount
            if not changed:
                raise KeyError(dispatch_id)

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

    def upsert_notification_incident(
        self,
        *,
        incident_key: str,
        mission_id: str,
        required_role: AgentRole,
        head_sha: str | None,
        reason: str,
        link: str | None,
        next_notification_at: str,
    ) -> int:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO notification_incidents (
                    incident_key, mission_id, required_role, head_sha, reason,
                    link, state, attempt_count, next_notification_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(incident_key) DO UPDATE SET
                    reason=excluded.reason,
                    link=COALESCE(excluded.link, notification_incidents.link),
                    updated_at=excluded.updated_at
                """,
                (
                    incident_key,
                    mission_id,
                    required_role.value,
                    head_sha,
                    reason,
                    link,
                    NotificationIncidentState.OPEN.value,
                    next_notification_at,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM notification_incidents WHERE incident_key=?",
                (incident_key,),
            ).fetchone()
            assert row is not None
            incident_id = int(row["id"])
            self._event(
                conn,
                mission_id,
                "NOTIFICATION_INCIDENT_OBSERVED",
                None,
                {
                    "incident_id": incident_id,
                    "incident_key": incident_key,
                    "required_role": required_role.value,
                    "head_sha": head_sha,
                    "reason": reason,
                },
            )
            conn.execute("COMMIT")
            return incident_id

    def list_notification_incidents(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM notification_incidents ORDER BY id"
                )
            )

    def list_open_notification_incidents(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM notification_incidents
                    WHERE state=? ORDER BY id
                    """,
                    (NotificationIncidentState.OPEN.value,),
                )
            )

    def due_notification_incidents(self, now_iso: str) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM notification_incidents
                    WHERE state=?
                      AND next_notification_at IS NOT NULL
                      AND next_notification_at<=?
                    ORDER BY next_notification_at, id
                    """,
                    (NotificationIncidentState.OPEN.value, now_iso),
                )
            )

    def set_notification_incident_state(
        self,
        incident_id: int,
        state: NotificationIncidentState,
        *,
        actor: str,
    ) -> None:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT mission_id FROM notification_incidents WHERE id=?",
                (incident_id,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(incident_id)
            acknowledged_at = (
                now if state is NotificationIncidentState.ACKNOWLEDGED else None
            )
            resolved_at = (
                now
                if state in {
                    NotificationIncidentState.RESOLVED,
                    NotificationIncidentState.STALE,
                }
                else None
            )
            conn.execute(
                """
                UPDATE notification_incidents
                SET state=?, acknowledged_at=COALESCE(?, acknowledged_at),
                    resolved_at=COALESCE(?, resolved_at),
                    next_notification_at=NULL, updated_at=?
                WHERE id=?
                """,
                (state.value, acknowledged_at, resolved_at, now, incident_id),
            )
            self._event(
                conn,
                row["mission_id"],
                "NOTIFICATION_INCIDENT_STATE",
                actor,
                {"incident_id": incident_id, "state": state.value},
            )
            conn.execute("COMMIT")

    def mark_notification_attempt(
        self,
        incident_id: int,
        *,
        next_notification_at: str | None,
        email_escalated: bool,
    ) -> int:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT mission_id, attempt_count
                FROM notification_incidents
                WHERE id=? AND state=?
                """,
                (incident_id, NotificationIncidentState.OPEN.value),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(incident_id)
            attempt = int(row["attempt_count"]) + 1
            conn.execute(
                """
                UPDATE notification_incidents
                SET attempt_count=?, next_notification_at=?,
                    email_escalated_at=CASE
                        WHEN ? THEN COALESCE(email_escalated_at, ?)
                        ELSE email_escalated_at
                    END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    attempt,
                    next_notification_at,
                    int(email_escalated),
                    now,
                    now,
                    incident_id,
                ),
            )
            self._event(
                conn,
                row["mission_id"],
                "NOTIFICATION_ATTEMPT",
                None,
                {
                    "incident_id": incident_id,
                    "attempt": attempt,
                    "next_notification_at": next_notification_at,
                    "email_escalated": email_escalated,
                },
            )
            conn.execute("COMMIT")
            return attempt

    def enqueue_notification_outbox(
        self,
        *,
        incident_id: int,
        channel: NotificationChannel,
        attempt_no: int,
        payload: dict,
        available_at: str,
    ) -> int:
        now = _iso_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO notification_outbox (
                    incident_id, channel, attempt_no, payload_json, state,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    channel.value,
                    attempt_no,
                    json.dumps(payload, sort_keys=True),
                    NotificationOutboxState.PENDING.value,
                    available_at,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM notification_outbox
                WHERE incident_id=? AND channel=? AND attempt_no=?
                """,
                (incident_id, channel.value, attempt_no),
            ).fetchone()
            assert row is not None
            outbox_id = int(row["id"])
            conn.execute("COMMIT")
            return outbox_id

    def list_notification_outbox(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM notification_outbox ORDER BY id"
                )
            )

    def due_notification_outbox(
        self,
        *,
        now_iso: str,
        channel: NotificationChannel | None = None,
    ) -> list[sqlite3.Row]:
        with self.connection() as conn:
            if channel is None:
                return list(
                    conn.execute(
                        """
                        SELECT o.*, i.mission_id, i.required_role, i.head_sha,
                               i.reason, i.link
                        FROM notification_outbox o
                        JOIN notification_incidents i ON i.id=o.incident_id
                        WHERE o.state=? AND o.available_at<=?
                        ORDER BY o.available_at, o.id
                        """,
                        (NotificationOutboxState.PENDING.value, now_iso),
                    )
                )
            return list(
                conn.execute(
                    """
                    SELECT o.*, i.mission_id, i.required_role, i.head_sha,
                           i.reason, i.link
                    FROM notification_outbox o
                    JOIN notification_incidents i ON i.id=o.incident_id
                    WHERE o.state=? AND o.available_at<=? AND o.channel=?
                    ORDER BY o.available_at, o.id
                    """,
                    (
                        NotificationOutboxState.PENDING.value,
                        now_iso,
                        channel.value,
                    ),
                )
            )

    def set_notification_outbox_state(
        self,
        outbox_id: int,
        state: NotificationOutboxState,
        *,
        error: str | None = None,
    ) -> None:
        now = _iso_now()
        with self.connection() as conn:
            changed = conn.execute(
                """
                UPDATE notification_outbox
                SET state=?, sent_at=CASE WHEN ?=? THEN ? ELSE sent_at END,
                    error=?, updated_at=?
                WHERE id=?
                """,
                (
                    state.value,
                    state.value,
                    NotificationOutboxState.SENT.value,
                    now,
                    error,
                    now,
                    outbox_id,
                ),
            ).rowcount
            if not changed:
                raise KeyError(outbox_id)

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
