"""Event-driven checkpoint-to-next-task dispatcher.

Consumes ``CHECKPOINT_RECORDED`` events from the ledger, enforces implementation
delivery proof, classifies each checkpoint and persists the successor
implementation task. All effects are local ledger writes; the dispatcher never
touches providers, remotes or production systems.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .models import MissionStatus
from .store import MissionStore

CHECKPOINT_EVENT = "CHECKPOINT_RECORDED"
CURSOR_NAME = "checkpoint-dispatch"
API_REQUIRED_MARKER = "api_required"
BLOCKING_STATES = frozenset({"BLOCKED", "NEEDS_DECISION", "TAKEOVER_BLOCKED_DIRTY"})
TERMINAL_PARENT_STATUSES = frozenset({MissionStatus.CERTIFIED, MissionStatus.COMPLETE})


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class DispatchClassification(StrEnum):
    BLOCKED = "BLOCKED"
    REWORK = "REWORK"
    READY_FOR_NEXT = "READY_FOR_NEXT"
    CONTINUE = "CONTINUE"


@dataclass(frozen=True)
class ImplementationProof:
    mission_id: str
    agent_id: str
    implementation_files: tuple[str, ...]
    tests: dict[str, Any]
    local_commit_sha: str | None
    pushed_branch_sha: str | None
    pr_head_sha: str | None
    pr_url: str | None
    pr_number: int | None = None
    api_endpoints: tuple[str, ...] = ()
    execution_id: str | None = None
    next_slice: str | None = None


@dataclass(frozen=True)
class DispatchDecision:
    event_id: int
    checkpoint_id: int | None
    mission_id: str
    classification: DispatchClassification
    reasons: tuple[str, ...]
    successor_mission_id: str | None = None


def tests_passed(tests: dict[str, Any]) -> bool:
    if tests.get("passed") is True:
        return True
    status = str(tests.get("status", "")).strip().upper()
    if status in {"PASS", "PASSED", "GREEN", "SUCCESS"}:
        return True
    total = tests.get("total")
    failed = tests.get("failed")
    return isinstance(total, int) and total > 0 and failed == 0


def tests_failed(tests: dict[str, Any]) -> bool:
    if tests.get("passed") is False:
        return True
    status = str(tests.get("status", "")).strip().upper()
    if status in {"FAIL", "FAILED", "RED", "ERROR"}:
        return True
    failed = tests.get("failed")
    return isinstance(failed, int) and failed > 0


def proof_reasons(
    proof: ImplementationProof | None,
    *,
    head_sha: str | None,
    api_required: bool,
) -> list[str]:
    """Return every reason the proof fails to certify ``head_sha`` for review."""
    if not head_sha:
        return ["checkpoint head_sha is required"]
    if proof is None:
        return [f"no implementation proof recorded for head {head_sha}"]

    reasons: list[str] = []
    if not proof.implementation_files:
        reasons.append("material implementation files are required")
    if not tests_passed(proof.tests):
        reasons.append("passing test evidence is required")
    if api_required and not proof.api_endpoints:
        reasons.append("API/endpoint evidence is required for this mission")
    if not proof.pr_url:
        reasons.append("pull request URL is required")
    shas = {
        "local commit": proof.local_commit_sha,
        "pushed branch": proof.pushed_branch_sha,
        "PR head": proof.pr_head_sha,
    }
    missing = [label for label, sha in shas.items() if not sha]
    if missing:
        reasons.append(f"delivery SHA missing: {', '.join(missing)}")
    elif len({*shas.values(), head_sha}) != 1:
        reasons.append(
            "delivery SHA mismatch: checkpoint head, local commit, pushed branch "
            "and PR head must match"
        )
    return reasons


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dispatch_cursors (
            name TEXT PRIMARY KEY,
            last_event_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS implementation_proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            execution_id TEXT,
            implementation_files_json TEXT NOT NULL,
            api_endpoints_json TEXT NOT NULL,
            tests_json TEXT NOT NULL,
            local_commit_sha TEXT,
            pushed_branch_sha TEXT,
            pr_number INTEGER,
            pr_url TEXT,
            pr_head_sha TEXT,
            next_slice TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dispatch_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL UNIQUE,
            checkpoint_id INTEGER,
            mission_id TEXT NOT NULL,
            agent_id TEXT,
            head_sha TEXT,
            classification TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            proof_id INTEGER,
            successor_mission_id TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS implementation_tasks (
            mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE CASCADE,
            root_mission_id TEXT NOT NULL,
            parent_mission_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            source_checkpoint_id INTEGER NOT NULL,
            source_head_sha TEXT NOT NULL,
            proof_id INTEGER NOT NULL,
            api_required INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (parent_mission_id, source_head_sha),
            UNIQUE (root_mission_id, sequence)
        );

        CREATE INDEX IF NOT EXISTS idx_proofs_mission_head
            ON implementation_proofs(mission_id, local_commit_sha, id);
        CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_mission
            ON dispatch_decisions(mission_id, id);
        """
    )


def _proof_from_row(row: sqlite3.Row) -> ImplementationProof:
    return ImplementationProof(
        mission_id=row["mission_id"],
        agent_id=row["agent_id"],
        execution_id=row["execution_id"],
        implementation_files=tuple(json.loads(row["implementation_files_json"])),
        api_endpoints=tuple(json.loads(row["api_endpoints_json"])),
        tests=json.loads(row["tests_json"]),
        local_commit_sha=row["local_commit_sha"],
        pushed_branch_sha=row["pushed_branch_sha"],
        pr_number=row["pr_number"],
        pr_url=row["pr_url"],
        pr_head_sha=row["pr_head_sha"],
        next_slice=row["next_slice"],
    )


def decision_payload(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["reasons"] = json.loads(item.pop("reasons_json"))
    return item


def proof_payload(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("implementation_files_json", "api_endpoints_json", "tests_json"):
        item[key.removesuffix("_json")] = json.loads(item.pop(key))
    return item


def task_payload(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["api_required"] = bool(item["api_required"])
    return item


class CheckpointDispatcher:
    def __init__(self, store: MissionStore, *, cursor_name: str = CURSOR_NAME) -> None:
        self.store = store
        self.cursor_name = cursor_name
        with self.store.connection() as conn:
            ensure_schema(conn)

    # -- proofs ---------------------------------------------------------------

    def record_proof(self, proof: ImplementationProof) -> int:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM missions WHERE mission_id=?", (proof.mission_id,)
            ).fetchone():
                conn.execute("ROLLBACK")
                raise KeyError(proof.mission_id)
            cursor = conn.execute(
                """
                INSERT INTO implementation_proofs (
                    mission_id, agent_id, execution_id, implementation_files_json,
                    api_endpoints_json, tests_json, local_commit_sha,
                    pushed_branch_sha, pr_number, pr_url, pr_head_sha, next_slice,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proof.mission_id,
                    proof.agent_id,
                    proof.execution_id,
                    json.dumps(list(proof.implementation_files)),
                    json.dumps(list(proof.api_endpoints)),
                    json.dumps(proof.tests, sort_keys=True),
                    proof.local_commit_sha,
                    proof.pushed_branch_sha,
                    proof.pr_number,
                    proof.pr_url,
                    proof.pr_head_sha,
                    proof.next_slice,
                    _iso_now(),
                ),
            )
            proof_id = int(cursor.lastrowid)
            MissionStore._event(
                conn,
                proof.mission_id,
                "IMPLEMENTATION_PROOF_RECORDED",
                proof.agent_id,
                {
                    "proof_id": proof_id,
                    "local_commit_sha": proof.local_commit_sha,
                    "pushed_branch_sha": proof.pushed_branch_sha,
                    "pr_head_sha": proof.pr_head_sha,
                    "pr_url": proof.pr_url,
                },
            )
            conn.execute("COMMIT")
            return proof_id

    # -- consumption ----------------------------------------------------------

    def run(self, *, limit: int = 100) -> list[DispatchDecision]:
        """Consume up to ``limit`` pending checkpoint events, each atomically."""
        decisions: list[DispatchDecision] = []
        for _ in range(max(limit, 0)):
            decision = self._consume_next()
            if decision is None:
                break
            decisions.append(decision)
        return decisions

    def _cursor(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT last_event_id FROM dispatch_cursors WHERE name=?",
            (self.cursor_name,),
        ).fetchone()
        return int(row["last_event_id"]) if row else 0

    def _advance(self, conn: sqlite3.Connection, event_id: int) -> None:
        conn.execute(
            """
            INSERT INTO dispatch_cursors (name, last_event_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at
            """,
            (self.cursor_name, event_id, _iso_now()),
        )

    def _consume_next(self) -> DispatchDecision | None:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                event = conn.execute(
                    """
                    SELECT * FROM events
                    WHERE id > ? AND event_type = ?
                    ORDER BY id
                    LIMIT 1
                    """,
                    (self._cursor(conn), CHECKPOINT_EVENT),
                ).fetchone()
                if event is None:
                    conn.execute("ROLLBACK")
                    return None
                decision = self._decide(conn, event)
                self._advance(conn, int(event["id"]))
                conn.execute("COMMIT")
                return decision
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _checkpoint_for(
        self, conn: sqlite3.Connection, event: sqlite3.Row
    ) -> sqlite3.Row | None:
        payload = json.loads(event["payload_json"])
        checkpoint_id = payload.get("checkpoint_id")
        if checkpoint_id is None:
            return None
        return conn.execute(
            "SELECT * FROM checkpoints WHERE id=? AND mission_id=?",
            (checkpoint_id, event["mission_id"]),
        ).fetchone()

    def _decide(self, conn: sqlite3.Connection, event: sqlite3.Row) -> DispatchDecision:
        event_id = int(event["id"])
        mission_id = str(event["mission_id"])
        checkpoint = self._checkpoint_for(conn, event)
        mission = conn.execute(
            "SELECT * FROM missions WHERE mission_id=?", (mission_id,)
        ).fetchone()

        if checkpoint is None or mission is None:
            # Unresolvable events (pre-dispatcher ledger rows, deleted missions) are
            # recorded as BLOCKED for visibility but never mutate mission status.
            reason = (
                "checkpoint event does not reference a recorded checkpoint"
                if checkpoint is None
                else f"mission not found: {mission_id}"
            )
            return self._persist(
                conn,
                event_id=event_id,
                checkpoint=checkpoint,
                mission_id=mission_id,
                classification=DispatchClassification.BLOCKED,
                reasons=[reason],
            )

        blockers = json.loads(checkpoint["blockers_json"])
        state = str(checkpoint["state"]).strip().upper()
        if blockers or state in BLOCKING_STATES or state.startswith("BLOCKED"):
            reasons = [str(item) for item in blockers] or [f"checkpoint state {state}"]
            return self._persist(
                conn,
                event_id=event_id,
                checkpoint=checkpoint,
                mission_id=mission_id,
                classification=DispatchClassification.BLOCKED,
                reasons=reasons,
                set_status=MissionStatus.BLOCKED,
            )

        if not checkpoint["next_task_requested"]:
            return self._persist(
                conn,
                event_id=event_id,
                checkpoint=checkpoint,
                mission_id=mission_id,
                classification=DispatchClassification.CONTINUE,
                reasons=["progress checkpoint did not request the next task"],
            )

        reasons: list[str] = []
        if checkpoint["dirty_count"] is None:
            reasons.append("checkpoint dirty_count is required")
        elif int(checkpoint["dirty_count"]) > 0:
            reasons.append(f"worktree is dirty ({checkpoint['dirty_count']} paths)")
        if tests_failed(json.loads(checkpoint["tests_json"])):
            reasons.append("checkpoint reports failing tests")

        head_sha = checkpoint["head_sha"]
        proof_row = None
        if head_sha:
            proof_row = conn.execute(
                """
                SELECT * FROM implementation_proofs
                WHERE mission_id=? AND local_commit_sha=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (mission_id, head_sha),
            ).fetchone()
        proof = _proof_from_row(proof_row) if proof_row else None
        api_required = self._api_required(conn, mission)
        reasons.extend(proof_reasons(proof, head_sha=head_sha, api_required=api_required))

        if MissionStatus(mission["status"]) in TERMINAL_PARENT_STATUSES:
            reasons.append(f"mission is already {mission['status']}")

        if reasons:
            return self._persist(
                conn,
                event_id=event_id,
                checkpoint=checkpoint,
                mission_id=mission_id,
                classification=DispatchClassification.REWORK,
                reasons=reasons,
                proof_id=int(proof_row["id"]) if proof_row else None,
            )

        if proof is None or proof_row is None:  # pragma: no cover - guarded by proof_reasons
            raise RuntimeError("verified dispatch requires a recorded proof")
        successor = self._persist_successor(
            conn,
            mission=mission,
            checkpoint=checkpoint,
            proof=proof,
            proof_id=int(proof_row["id"]),
            api_required=api_required,
        )
        return self._persist(
            conn,
            event_id=event_id,
            checkpoint=checkpoint,
            mission_id=mission_id,
            classification=DispatchClassification.READY_FOR_NEXT,
            reasons=["implementation proof verified; successor task queued"],
            proof_id=int(proof_row["id"]),
            successor_mission_id=successor,
            set_status=MissionStatus.IN_REVIEW,
        )

    @staticmethod
    def _api_required(conn: sqlite3.Connection, mission: sqlite3.Row) -> bool:
        task = conn.execute(
            "SELECT api_required FROM implementation_tasks WHERE mission_id=?",
            (mission["mission_id"],),
        ).fetchone()
        if task is not None:
            return bool(task["api_required"])
        acceptance = json.loads(mission["acceptance_json"] or "[]")
        return any(str(item).strip().lower() == API_REQUIRED_MARKER for item in acceptance)

    def _persist_successor(
        self,
        conn: sqlite3.Connection,
        *,
        mission: sqlite3.Row,
        checkpoint: sqlite3.Row,
        proof: ImplementationProof,
        proof_id: int,
        api_required: bool,
    ) -> str:
        parent_id = str(mission["mission_id"])
        head_sha = str(checkpoint["head_sha"])
        existing = conn.execute(
            """
            SELECT mission_id FROM implementation_tasks
            WHERE parent_mission_id=? AND source_head_sha=?
            """,
            (parent_id, head_sha),
        ).fetchone()
        if existing:
            return str(existing["mission_id"])

        parent_task = conn.execute(
            "SELECT root_mission_id FROM implementation_tasks WHERE mission_id=?",
            (parent_id,),
        ).fetchone()
        root_id = str(parent_task["root_mission_id"]) if parent_task else parent_id
        sequence = int(
            conn.execute(
                "SELECT coalesce(max(sequence), 0) + 1 AS next FROM implementation_tasks "
                "WHERE root_mission_id=?",
                (root_id,),
            ).fetchone()["next"]
        )
        successor_id = f"{root_id}-S{sequence}"
        while conn.execute(
            "SELECT 1 FROM missions WHERE mission_id=?", (successor_id,)
        ).fetchone():
            sequence += 1
            successor_id = f"{root_id}-S{sequence}"
        goal = proof.next_slice or f"Next implementation slice after {parent_id}: {mission['goal']}"
        now = _iso_now()
        conn.execute(
            """
            INSERT INTO missions (
                mission_id, repository, goal, status, branch, worktree,
                base_sha, head_sha, required_approval, acceptance_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                successor_id,
                mission["repository"],
                goal,
                MissionStatus.QUEUED.value,
                head_sha,
                head_sha,
                mission["required_approval"],
                mission["acceptance_json"],
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO implementation_tasks (
                mission_id, root_mission_id, parent_mission_id, sequence,
                source_checkpoint_id, source_head_sha, proof_id, api_required,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                successor_id,
                root_id,
                parent_id,
                sequence,
                int(checkpoint["id"]),
                head_sha,
                proof_id,
                int(api_required),
                now,
            ),
        )
        MissionStore._event(
            conn,
            successor_id,
            "SUCCESSOR_TASK_QUEUED",
            None,
            {
                "parent_mission_id": parent_id,
                "root_mission_id": root_id,
                "sequence": sequence,
                "base_sha": head_sha,
                "proof_id": proof_id,
            },
        )
        return successor_id

    def _persist(
        self,
        conn: sqlite3.Connection,
        *,
        event_id: int,
        checkpoint: sqlite3.Row | None,
        mission_id: str,
        classification: DispatchClassification,
        reasons: list[str],
        proof_id: int | None = None,
        successor_mission_id: str | None = None,
        set_status: MissionStatus | None = None,
    ) -> DispatchDecision:
        checkpoint_id = int(checkpoint["id"]) if checkpoint is not None else None
        conn.execute(
            """
            INSERT INTO dispatch_decisions (
                event_id, checkpoint_id, mission_id, agent_id, head_sha,
                classification, reasons_json, proof_id, successor_mission_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                checkpoint_id,
                mission_id,
                checkpoint["agent_id"] if checkpoint is not None else None,
                checkpoint["head_sha"] if checkpoint is not None else None,
                classification.value,
                json.dumps(reasons),
                proof_id,
                successor_mission_id,
                _iso_now(),
            ),
        )
        if set_status is not None:
            conn.execute(
                "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                (set_status.value, _iso_now(), mission_id),
            )
            MissionStore._event(
                conn, mission_id, "STATUS_CHANGED", None, {"status": set_status.value}
            )
        MissionStore._event(
            conn,
            mission_id,
            "DISPATCH_DECIDED",
            checkpoint["agent_id"] if checkpoint is not None else None,
            {
                "event_id": event_id,
                "checkpoint_id": checkpoint_id,
                "classification": classification.value,
                "reasons": reasons,
                "successor_mission_id": successor_mission_id,
            },
        )
        return DispatchDecision(
            event_id=event_id,
            checkpoint_id=checkpoint_id,
            mission_id=mission_id,
            classification=classification,
            reasons=tuple(reasons),
            successor_mission_id=successor_mission_id,
        )

    # -- read models ----------------------------------------------------------

    def decisions(
        self, *, mission_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            if mission_id:
                rows = conn.execute(
                    "SELECT * FROM dispatch_decisions WHERE mission_id=? "
                    "ORDER BY id DESC LIMIT ?",
                    (mission_id, limit),
                )
            else:
                rows = conn.execute(
                    "SELECT * FROM dispatch_decisions ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return [decision_payload(row) for row in rows]

    def tasks(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            query = """
                SELECT t.*, m.goal, m.repository, m.status AS mission_status, m.base_sha
                FROM implementation_tasks t
                JOIN missions m ON m.mission_id = t.mission_id
            """
            if status:
                rows = conn.execute(
                    query + " WHERE m.status=? ORDER BY t.created_at, t.mission_id",
                    (status,),
                )
            else:
                rows = conn.execute(query + " ORDER BY t.created_at, t.mission_id")
            return [task_payload(row) for row in rows]

    def proofs(self, mission_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM implementation_proofs WHERE mission_id=? ORDER BY id DESC",
                (mission_id,),
            )
            return [proof_payload(row) for row in rows]

    def state(self, *, recent: int = 20) -> dict[str, Any]:
        with self.store.connection() as conn:
            cursor = self._cursor(conn)
            pending = int(
                conn.execute(
                    "SELECT count(*) AS n FROM events WHERE id > ? AND event_type = ?",
                    (cursor, CHECKPOINT_EVENT),
                ).fetchone()["n"]
            )
            counts = {item.value: 0 for item in DispatchClassification}
            for row in conn.execute(
                "SELECT classification, count(*) AS n FROM dispatch_decisions "
                "GROUP BY classification"
            ):
                counts[row["classification"]] = int(row["n"])
        return {
            "cursor": {"name": self.cursor_name, "last_event_id": cursor},
            "pending_checkpoint_events": pending,
            "classification_counts": counts,
            "recent_decisions": self.decisions(limit=recent),
            "queued_tasks": self.tasks(status=MissionStatus.QUEUED.value),
        }
