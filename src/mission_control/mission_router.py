from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .store import MissionStore


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


@dataclass(frozen=True)
class AtomicTaskLease:
    task_id: str
    agent_id: str
    lease_token: str
    heartbeat_at: str
    expires_at: str
    takeover: bool = False


class RouterConflict(RuntimeError):
    pass


class RouterNotReady(RuntimeError):
    pass


class RouterLeaseError(RuntimeError):
    pass


class MissionRouter:
    """Atomic-task router layered on top of MissionStore.

    GitHub/CI evidence is authoritative for certification. Linear is intentionally
    absent from the state-transition path and is treated as a downstream sync surface.
    """

    WORK_COMPLETE_STATES = frozenset({"IN_REVIEW", "MERGED", "CERTIFIED"})
    CERTIFIED_STATE = "CERTIFIED"

    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def initialize(self) -> None:
        self.store.initialize()
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS router_areas (
                    area_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    UNIQUE(repository, position),
                    UNIQUE(repository, name)
                );

                CREATE TABLE IF NOT EXISTS router_subareas (
                    subarea_id TEXT PRIMARY KEY,
                    area_id TEXT NOT NULL REFERENCES router_areas(area_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    UNIQUE(area_id, position),
                    UNIQUE(area_id, name)
                );

                CREATE TABLE IF NOT EXISTS router_tasks (
                    task_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    area_id TEXT NOT NULL REFERENCES router_areas(area_id),
                    subarea_id TEXT NOT NULL REFERENCES router_subareas(subarea_id),
                    mission_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    atomic_contract_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50,
                    work_state TEXT NOT NULL DEFAULT 'READY',
                    work_head_sha TEXT,
                    certified INTEGER NOT NULL DEFAULT 0,
                    certified_head_sha TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_router_tasks_ready
                    ON router_tasks(repository, work_state, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_router_tasks_mission
                    ON router_tasks(mission_id, task_id);

                CREATE TABLE IF NOT EXISTS router_task_leases (
                    task_id TEXT PRIMARY KEY REFERENCES router_tasks(task_id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL UNIQUE,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS router_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES router_tasks(task_id) ON DELETE CASCADE,
                    evidence_type TEXT NOT NULL,
                    head_sha TEXT,
                    status TEXT NOT NULL,
                    url TEXT,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_router_evidence_task
                    ON router_evidence(task_id, id);
                """
            )

    def register_hierarchy(self, repository: str, areas: list[dict[str, Any]]) -> None:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for area_pos, area in enumerate(areas, start=1):
                    area_id = str(area.get("area_id") or f"{repository}:area:{area_pos:02d}")
                    conn.execute(
                        """
                        INSERT INTO router_areas(area_id, repository, position, name, contract_json)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(area_id) DO UPDATE SET
                            position=excluded.position,
                            name=excluded.name,
                            contract_json=excluded.contract_json
                        """,
                        (
                            area_id,
                            repository,
                            area_pos,
                            str(area["name"]),
                            json.dumps(area.get("contract") or {}, sort_keys=True),
                        ),
                    )
                    for sub_pos, subarea in enumerate(area.get("subareas") or [], start=1):
                        subarea_id = str(
                            subarea.get("subarea_id") or f"{area_id}:sub:{sub_pos:02d}"
                        )
                        conn.execute(
                            """
                            INSERT INTO router_subareas(
                                subarea_id, area_id, position, name, contract_json
                            )
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(subarea_id) DO UPDATE SET
                                position=excluded.position,
                                name=excluded.name,
                                contract_json=excluded.contract_json
                            """,
                            (
                                subarea_id,
                                area_id,
                                sub_pos,
                                str(subarea["name"]),
                                json.dumps(subarea.get("contract") or {}, sort_keys=True),
                            ),
                        )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def create_atomic_task(
        self,
        *,
        task_id: str,
        repository: str,
        area_id: str,
        subarea_id: str,
        mission_id: str,
        title: str,
        atomic_contract: dict[str, Any],
        priority: int = 50,
    ) -> None:
        if not 0 <= priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        now = _iso()
        with self.store.connection() as conn:
            area = conn.execute(
                "SELECT repository FROM router_areas WHERE area_id=?", (area_id,)
            ).fetchone()
            subarea = conn.execute(
                "SELECT area_id FROM router_subareas WHERE subarea_id=?", (subarea_id,)
            ).fetchone()
            if not area or area["repository"] != repository:
                raise KeyError(area_id)
            if not subarea or subarea["area_id"] != area_id:
                raise KeyError(subarea_id)
            conn.execute(
                """
                INSERT INTO router_tasks(
                    task_id, repository, area_id, subarea_id, mission_id, title,
                    atomic_contract_json, priority, work_state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?)
                """,
                (
                    task_id,
                    repository,
                    area_id,
                    subarea_id,
                    mission_id,
                    title,
                    json.dumps(atomic_contract, sort_keys=True),
                    priority,
                    now,
                    now,
                ),
            )

    def _task(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM router_tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        return row

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        *,
        ttl_seconds: int = 600,
    ) -> AtomicTaskLease:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be between 30 and 3600")
        now = _now()
        expiry = now + timedelta(seconds=ttl_seconds)
        token = uuid.uuid4().hex
        takeover = False
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                task = self._task(conn, task_id)
                if task["certified"]:
                    raise RouterNotReady("task is already certified")
                lease = conn.execute(
                    "SELECT * FROM router_task_leases WHERE task_id=?", (task_id,)
                ).fetchone()
                if lease:
                    if datetime.fromisoformat(lease["expires_at"]) > now:
                        raise RouterConflict(
                            f"task already leased by {lease['agent_id']}"
                        )
                    takeover = True
                    conn.execute("DELETE FROM router_task_leases WHERE task_id=?", (task_id,))
                if task["work_state"] not in {"READY", "CLAIMED", "WORKING"}:
                    raise RouterNotReady(f"task state is {task['work_state']}")
                conn.execute(
                    """
                    INSERT INTO router_task_leases(
                        task_id, agent_id, lease_token, acquired_at, heartbeat_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, agent_id, token, _iso(now), _iso(now), _iso(expiry)),
                )
                conn.execute(
                    "UPDATE router_tasks SET work_state='CLAIMED', updated_at=? WHERE task_id=?",
                    (_iso(now), task_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return AtomicTaskLease(
            task_id=task_id,
            agent_id=agent_id,
            lease_token=token,
            heartbeat_at=_iso(now),
            expires_at=_iso(expiry),
            takeover=takeover,
        )

    def heartbeat(
        self,
        task_id: str,
        lease_token: str,
        *,
        ttl_seconds: int = 600,
    ) -> AtomicTaskLease:
        if ttl_seconds < 30 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be between 30 and 3600")
        now = _now()
        expiry = now + timedelta(seconds=ttl_seconds)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                lease = conn.execute(
                    "SELECT * FROM router_task_leases WHERE task_id=?", (task_id,)
                ).fetchone()
                if not lease or lease["lease_token"] != lease_token:
                    raise RouterLeaseError("lease token does not own task")
                if datetime.fromisoformat(lease["expires_at"]) <= now:
                    conn.execute("DELETE FROM router_task_leases WHERE task_id=?", (task_id,))
                    conn.execute(
                        "UPDATE router_tasks SET work_state='READY', updated_at=? WHERE task_id=?",
                        (_iso(now), task_id),
                    )
                    raise RouterLeaseError("lease expired and task was reclaimed")
                conn.execute(
                    """
                    UPDATE router_task_leases
                    SET heartbeat_at=?, expires_at=?
                    WHERE task_id=?
                    """,
                    (_iso(now), _iso(expiry), task_id),
                )
                conn.execute(
                    "UPDATE router_tasks SET work_state='WORKING', updated_at=? WHERE task_id=?",
                    (_iso(now), task_id),
                )
                conn.execute("COMMIT")
                return AtomicTaskLease(
                    task_id=task_id,
                    agent_id=lease["agent_id"],
                    lease_token=lease_token,
                    heartbeat_at=_iso(now),
                    expires_at=_iso(expiry),
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def complete_work(self, task_id: str, lease_token: str, *, head_sha: str) -> None:
        if not head_sha.strip():
            raise ValueError("head_sha is required")
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                lease = conn.execute(
                    "SELECT * FROM router_task_leases WHERE task_id=?", (task_id,)
                ).fetchone()
                if not lease or lease["lease_token"] != lease_token:
                    raise RouterLeaseError("lease token does not own task")
                if datetime.fromisoformat(lease["expires_at"]) <= _now():
                    raise RouterLeaseError("lease expired")
                conn.execute(
                    """
                    UPDATE router_tasks
                    SET work_state='IN_REVIEW', work_head_sha=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (head_sha, _iso(), task_id),
                )
                conn.execute("DELETE FROM router_task_leases WHERE task_id=?", (task_id,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def reclaim_expired(self) -> list[str]:
        now = _now()
        reclaimed: list[str] = []
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = list(
                    conn.execute(
                        "SELECT task_id FROM router_task_leases WHERE expires_at <= ?",
                        (_iso(now),),
                    )
                )
                for row in rows:
                    reclaimed.append(row["task_id"])
                    conn.execute(
                        "DELETE FROM router_task_leases WHERE task_id=?", (row["task_id"],)
                    )
                    conn.execute(
                        """
                        UPDATE router_tasks
                        SET work_state='READY', updated_at=?
                        WHERE task_id=? AND certified=0
                        """,
                        (_iso(now), row["task_id"]),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return reclaimed

    def move_agents(
        self,
        agent_ids: list[str],
        *,
        repository: str | None = None,
        ttl_seconds: int = 600,
    ) -> list[AtomicTaskLease]:
        self.reclaim_expired()
        leases: list[AtomicTaskLease] = []
        with self.store.connection() as conn:
            active_agents = {
                row["agent_id"]
                for row in conn.execute(
                    "SELECT agent_id FROM router_task_leases WHERE expires_at > ?",
                    (_iso(),),
                )
            }
        for agent_id in agent_ids:
            if agent_id in active_agents:
                continue
            task = self.next_ready_task(repository=repository)
            if not task:
                break
            leases.append(
                self.claim_task(task["task_id"], agent_id, ttl_seconds=ttl_seconds)
            )
        return leases

    def next_ready_task(self, *, repository: str | None = None) -> dict[str, Any] | None:
        query = """
            SELECT t.*, a.position AS area_position, s.position AS subarea_position
            FROM router_tasks t
            JOIN router_areas a ON a.area_id=t.area_id
            JOIN router_subareas s ON s.subarea_id=t.subarea_id
            LEFT JOIN router_task_leases l ON l.task_id=t.task_id
            WHERE t.work_state='READY' AND t.certified=0 AND l.task_id IS NULL
        """
        args: list[Any] = []
        if repository:
            query += " AND t.repository=?"
            args.append(repository)
        query += " ORDER BY t.priority, a.position, s.position, t.created_at, t.task_id LIMIT 1"
        with self.store.connection() as conn:
            row = conn.execute(query, args).fetchone()
            return self._task_payload(row) if row else None

    def record_reconciliation(
        self,
        task_id: str,
        *,
        head_sha: str,
        pr_merged: bool,
        ci_green: bool,
        post_merge_green: bool,
        pr_url: str | None = None,
        ci_url: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply observed GitHub/CI truth and advance certification only from evidence."""
        if not head_sha.strip():
            raise ValueError("head_sha is required")
        now = _iso()
        evidence = {
            "github_pr_merged": pr_merged,
            "ci_green": ci_green,
            "post_merge_green": post_merge_green,
        }
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                task = self._task(conn, task_id)
                expected_head = task["work_head_sha"]
                if expected_head and expected_head != head_sha:
                    raise RouterConflict(
                        f"observed head {head_sha} does not match work head {expected_head}"
                    )
                for evidence_type, passed in evidence.items():
                    conn.execute(
                        """
                        INSERT INTO router_evidence(
                            task_id, evidence_type, head_sha, status, url, payload_json, observed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            evidence_type,
                            head_sha,
                            "PASS" if passed else "FAIL",
                            pr_url if evidence_type == "github_pr_merged" else ci_url,
                            json.dumps(payload or {}, sort_keys=True),
                            now,
                        ),
                    )
                certified = pr_merged and ci_green and post_merge_green
                state = "CERTIFIED" if certified else ("MERGED" if pr_merged else "IN_REVIEW")
                conn.execute(
                    """
                    UPDATE router_tasks
                    SET work_state=?, work_head_sha=COALESCE(work_head_sha, ?),
                        certified=?, certified_head_sha=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (
                        state,
                        head_sha,
                        int(certified),
                        head_sha if certified else None,
                        now,
                        task_id,
                    ),
                )
                conn.execute("DELETE FROM router_task_leases WHERE task_id=?", (task_id,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.task(task_id)

    def task(self, task_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            return self._task_payload(self._task(conn, task_id))

    def tasks(
        self,
        *,
        repository: str | None = None,
        work_state: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT t.*, l.agent_id, l.lease_token, l.heartbeat_at, l.expires_at
            FROM router_tasks t
            LEFT JOIN router_task_leases l ON l.task_id=t.task_id
            WHERE 1=1
        """
        args: list[Any] = []
        if repository:
            query += " AND t.repository=?"
            args.append(repository)
        if work_state:
            query += " AND t.work_state=?"
            args.append(work_state)
        query += " ORDER BY t.priority, t.created_at, t.task_id"
        with self.store.connection() as conn:
            return [self._task_payload(row) for row in conn.execute(query, args)]

    def hierarchy(self, repository: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            areas = list(
                conn.execute(
                    "SELECT * FROM router_areas WHERE repository=? ORDER BY position",
                    (repository,),
                )
            )
            result: list[dict[str, Any]] = []
            for area in areas:
                subareas = list(
                    conn.execute(
                        "SELECT * FROM router_subareas WHERE area_id=? ORDER BY position",
                        (area["area_id"],),
                    )
                )
                result.append(
                    {
                        "area_id": area["area_id"],
                        "position": area["position"],
                        "name": area["name"],
                        "contract": json.loads(area["contract_json"]),
                        "subareas": [
                            {
                                "subarea_id": sub["subarea_id"],
                                "position": sub["position"],
                                "name": sub["name"],
                                "contract": json.loads(sub["contract_json"]),
                            }
                            for sub in subareas
                        ],
                    }
                )
            return result

    def dashboard(self, repository: str | None = None) -> dict[str, Any]:
        tasks = self.tasks(repository=repository)
        total = len(tasks)
        work_complete = sum(
            1 for task in tasks if task["work_state"] in self.WORK_COMPLETE_STATES
        )
        certified = sum(1 for task in tasks if task["certified"])
        active = [
            task
            for task in tasks
            if task.get("agent_id") and task.get("expires_at")
        ]
        blocked = [task for task in tasks if task["work_state"] == "BLOCKED"]
        return {
            "repository": repository,
            "totals": {
                "atomic_tasks": total,
                "work_complete": work_complete,
                "certified": certified,
                "active_leases": len(active),
                "blocked": len(blocked),
            },
            "progress": {
                "work_in_progress_pct": round((work_complete / total * 100), 2)
                if total
                else 0.0,
                "certified_pct": round((certified / total * 100), 2) if total else 0.0,
            },
            "active_leases": active,
            "blocked_tasks": blocked,
        }

    @staticmethod
    def _task_payload(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["atomic_contract"] = json.loads(item.pop("atomic_contract_json"))
        item["certified"] = bool(item["certified"])
        return item
