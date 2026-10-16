from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

STATES = {
    "QUEUED",
    "ASSIGNED",
    "LEASED",
    "STARTING",
    "ACTIVE",
    "TESTING",
    "CHECKPOINTED",
    "IN_REVIEW",
    "CERTIFICATION",
    "COMPLETED",
    "BLOCKED",
    "STALLED",
    "STOPPED",
}


class ConvergenceStore:
    def __init__(self, store):
        self.store = store

    def initialize(self):
        with self.store.connection() as c:
            c.executescript("""
   CREATE TABLE IF NOT EXISTS mission_intents_v2(
    mission_id TEXT PRIMARY KEY,product_goal TEXT NOT NULL,business_reason TEXT NOT NULL,
    repository TEXT,target_branch TEXT,current_stage TEXT NOT NULL,created_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS task_leases_v2(
    task_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,lease_token TEXT NOT NULL,state TEXT NOT NULL,
    current_sha TEXT,changed_files_count INTEGER NOT NULL DEFAULT 0,heartbeat_at TEXT NOT NULL,expires_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS certifications_v2(
    certification_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,exact_sha TEXT NOT NULL,
    test_pass_rate REAL NOT NULL,artifact_url TEXT NOT NULL,ci_job_id TEXT,status TEXT NOT NULL,certified_at TEXT NOT NULL);
   """)

    def create_mission(self, body):
        mid = "MISSION-" + datetime.now(UTC).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()
        now = datetime.now(UTC).isoformat()
        with self.store.connection() as c:
            c.execute(
                "INSERT INTO mission_intents_v2 VALUES(?,?,?,?,?,?,?)",
                (
                    mid,
                    body["product_goal"],
                    body["business_reason"],
                    body.get("target_repository") or body.get("repository"),
                    body.get("target_branch"),
                    "IMPLEMENTATION",
                    now,
                ),
            )
        return {
            "mission_id": mid,
            "product_goal": body["product_goal"],
            "business_reason": body["business_reason"],
            "current_stage": "IMPLEMENTATION",
            "progress_dimensions": {
                k: 0
                for k in (
                    "existence",
                    "completeness",
                    "correctness",
                    "integration",
                    "security",
                    "operability",
                )
            },
        }

    def missions(self, status=None, limit=20, offset=0):
        with self.store.connection() as c:
            rows = c.execute(
                "SELECT * FROM mission_intents_v2 ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        items = [
            {
                "mission_id": r["mission_id"],
                "product_goal": r["product_goal"],
                "business_reason": r["business_reason"],
                "current_stage": r["current_stage"],
                "progress_dimensions": {
                    k: 0
                    for k in (
                        "existence",
                        "completeness",
                        "correctness",
                        "integration",
                        "security",
                        "operability",
                    )
                },
            }
            for r in rows
        ]
        return {"total": len(items), "items": items}

    def lease(self, task_id, agent_id, heartbeat_interval_sec=30):
        now = datetime.now(UTC)
        expiry = now + timedelta(seconds=max(90, heartbeat_interval_sec * 3))
        with self.store.connection() as c:
            old = c.execute("SELECT * FROM task_leases_v2 WHERE task_id=?", (task_id,)).fetchone()
            if (
                old
                and datetime.fromisoformat(old["expires_at"]) > now
                and old["agent_id"] != agent_id
            ):
                raise ValueError("TASK_ALREADY_LEASED")
            token = secrets.token_urlsafe(24)
            c.execute(
                "INSERT INTO task_leases_v2(task_id,agent_id,lease_token,state,heartbeat_at,expires_at) VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET agent_id=excluded.agent_id,lease_token=excluded.lease_token,state=excluded.state,heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at",
                (task_id, agent_id, token, "LEASED", now.isoformat(), expiry.isoformat()),
            )
        return {"leased_to": agent_id, "lease_token": token, "expires_at": expiry.isoformat()}

    def heartbeat(self, body):
        now = datetime.now(UTC)
        if body.get("status") not in STATES:
            raise ValueError("INVALID_TASK_STATE")
        token = body.get("lease_token")
        if not token:
            raise ValueError("LEASE_TOKEN_REQUIRED")
        with self.store.connection() as c:
            row = c.execute(
                "SELECT * FROM task_leases_v2 WHERE task_id=? AND agent_id=? AND lease_token=?",
                (body["task_id"], body["agent_id"], token),
            ).fetchone()
            if not row or datetime.fromisoformat(row["expires_at"]) <= now:
                raise KeyError("LEASE_EXPIRED")
            expiry = now + timedelta(seconds=90)
            c.execute(
                "UPDATE task_leases_v2 SET state=?,current_sha=?,changed_files_count=?,heartbeat_at=?,expires_at=? WHERE task_id=?",
                (
                    body["status"],
                    body["current_sha"],
                    int(body.get("changed_files_count", 0)),
                    now.isoformat(),
                    expiry.isoformat(),
                    body["task_id"],
                ),
            )
        return {
            "acknowledged": True,
            "next_heartbeat_due": (now + timedelta(seconds=30)).isoformat(),
            "lease_expires_at": expiry.isoformat(),
        }

    def certify(self, body):
        rate = float(body["test_pass_rate"])
        status = "CERTIFIED" if rate == 100.0 else "REJECTED"
        cid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.store.connection() as c:
            c.execute(
                "INSERT INTO certifications_v2 VALUES(?,?,?,?,?,?,?,?)",
                (
                    cid,
                    body["task_id"],
                    body["exact_sha"],
                    rate,
                    body["artifact_url"],
                    body.get("ci_job_id"),
                    status,
                    now,
                ),
            )
            if status == "CERTIFIED":
                updated = c.execute(
                    "UPDATE atomic_tasks SET certified=1,completion_percent=100 WHERE task_id=?",
                    (body["task_id"],),
                ).rowcount
                if updated != 1:
                    raise ValueError("TASK_NOT_FOUND")
        return {
            "certification_id": cid,
            "task_id": body["task_id"],
            "exact_sha": body["exact_sha"],
            "certified_at": now,
            "status": status,
        }
