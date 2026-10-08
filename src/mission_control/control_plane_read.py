"""Strict read-only Codestra development control-plane projection for Mission Control.

The source of truth is the existing PostgreSQL control-plane schema. This module
never writes, updates, claims, heartbeats or manufactures evidence. It refuses
to substitute a stale SQLite snapshot when the PostgreSQL source is unavailable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime


class SourceUnavailable(RuntimeError):
    pass


class ControlPlaneReadModel:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn if dsn is not None else os.getenv("CODESTRA_CONTROL_READ_DSN", "")

    @contextmanager
    def connection(self):
        if not self.dsn:
            raise SourceUnavailable("read_only_control_plane_dsn_not_configured")
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:
            raise SourceUnavailable("read_only_postgres_driver_missing") from exc
        try:
            conn = psycopg2.connect(
                self.dsn,
                connect_timeout=3,
                application_name="codestra-mission-control-readonly",
                options="-c statement_timeout=5000",
            )
        except psycopg2.Error as exc:
            raise SourceUnavailable("read_only_control_plane_unavailable") from exc
        try:
            conn.set_session(readonly=True, autocommit=False)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
        except psycopg2.Error as exc:
            raise SourceUnavailable("read_only_control_plane_query_failed") from exc
        finally:
            conn.rollback()
            conn.close()

    @staticmethod
    def _text(row, key):
        value = row.get(key)
        return str(value) if value is not None else None

    def repositories(self):
        with self.connection() as c:
            c.execute("""
                SELECT p.product, p.repository, p.production_go, p.live_capabilities_enabled,
                       p.external_effects_enabled, p.updated_at,
                       count(w.workstation_id)::integer AS total_workstations,
                       count(w.workstation_id) FILTER (WHERE w.status = 'CERTIFIED')::integer AS certified_workstations,
                       count(w.workstation_id) FILTER (
                           WHERE w.agent IS NOT NULL AND w.heartbeat_at > now() - interval '90 seconds'
                             AND w.lease_state = 'ACTIVE'
                       )::integer AS active_agents
                FROM products p
                LEFT JOIN workstations w ON w.product = p.product
                GROUP BY p.product,p.repository,p.production_go,
                         p.live_capabilities_enabled,p.external_effects_enabled,p.updated_at
                ORDER BY lower(p.product)
            """)
            data = c.fetchall()
        result = []
        for r in data:
            count = r["total_workstations"]
            done = r["certified_workstations"]
            result.append(
                {
                    "repository": r["product"],
                    "full_name": r["repository"],
                    "planning": "REGISTERED_CONTROL_PLANE",
                    "sync_state": "UNVERIFIED",
                    "ci_state": "UNVERIFIED",
                    # No GitHub PR authority is connected here. Null is not zero.
                    "open_prs": None,
                    "active_agents": r["active_agents"],
                    "wip_percent": round(100 * done / count, 1) if count else 0,
                    "certified_tasks": done,
                    "total_tasks": count,
                    "total_workstations": count,
                    "certified_workstations": done,
                    "production_go": r["production_go"],
                    "live_capabilities_enabled": r["live_capabilities_enabled"],
                    "external_effects_enabled": r["external_effects_enabled"],
                    "remote_head_sha": None,
                    "last_synced_at": self._text(r, "updated_at"),
                    "pr_source": "NOT_CONNECTED",
                    "ci_source": "NOT_CONNECTED",
                    "progress_source": "PostgreSQL certified-workstation counts",
                    "progress_dimensions": {
                        "existence": 100 if count else None,
                        "completeness": round(100 * done / count, 1) if count else None,
                        "correctness": round(100 * done / count, 1) if count else None,
                        "integration": None,
                        "security": None,
                        "operability": None,
                    },
                    "progress_dimension_basis": {
                        "existence": "registered workstation inventory",
                        "completeness": "workstations with exact certification",
                        "correctness": "control-plane CERTIFIED status only",
                        "integration": "GitHub CI has not been independently fetched",
                        "security": "not yet certified",
                        "operability": "not yet certified",
                    },
                }
            )
        return result

    def repository(self, repository: str):
        """Read-only structural graph; no invented PR or CI status."""
        with self.connection() as c:
            c.execute(
                "SELECT section_key,status,branch,exact_sha FROM sections "
                "WHERE product=%s ORDER BY order_index",
                (repository,),
            )
            sections = c.fetchall()
            c.execute(
                "SELECT workstation_id, section, subsection, branch, status, "
                "local_sha,remote_sha,heartbeat_at,agent,blocker "
                "FROM workstations WHERE product=%s ORDER BY order_index LIMIT 500",
                (repository,),
            )
            workstations = c.fetchall()
        graph = []
        for section in sections:
            children = [
                {
                    "node_id": w["workstation_id"],
                    "title": w["subsection"],
                    "status": w["status"],
                    "branch": w["branch"],
                    "exact_sha": w["remote_sha"],
                    "last_heartbeat": self._text(w, "heartbeat_at"),
                    "owner": w["agent"],
                    "blocker": w["blocker"],
                    "children": [],
                }
                for w in workstations
                if w["section"] == section["section_key"]
            ]
            graph.append(
                {
                    "node_id": section["section_key"],
                    "title": section["section_key"],
                    "status": section["status"],
                    "branch": section["branch"],
                    "exact_sha": section["exact_sha"],
                    "children": children,
                }
            )
        return {
            "repository": repository,
            "graph": graph,
            "source": "postgres_control_plane_readonly",
            "pr_summary": {
                "total": None,
                "ci_green": None,
                "merged": None,
                "post_merge_verified": None,
            },
        }

    def agents(self):
        with self.connection() as c:
            c.execute("""
                SELECT workstation_id, product, agent, status, branch,
                       worktree, current_atomic_task, heartbeat_at, lease_state
                FROM workstations WHERE agent IS NOT NULL
                ORDER BY heartbeat_at DESC NULLS LAST LIMIT 250
            """)
            rows = c.fetchall()
        now = datetime.now(UTC)
        return [
            {
                "agent_id": r["agent"],
                "provider": "control-plane",
                "agent_type": "WORKSTATION",
                "repository": r["product"],
                "workstation": r["workstation_id"],
                "task_id": r["current_atomic_task"],
                "branch": r["branch"],
                "heartbeat_at": self._text(r, "heartbeat_at"),
                "state": (
                    "ACTIVE"
                    if r["heartbeat_at"] is not None
                    and (now - r["heartbeat_at"]).total_seconds() <= 90
                    and r["lease_state"] == "ACTIVE"
                    else "OFFLINE"
                ),
                "live": bool(
                    r["heartbeat_at"]
                    and 0 <= (now - r["heartbeat_at"]).total_seconds() <= 90
                    and r["lease_state"] == "ACTIVE"
                ),
            }
            for r in rows
        ]

    def tasks(self, repository: str):
        with self.connection() as c:
            c.execute(
                """
                SELECT t.task_id, t.workstation_id, t.title, t.status, t.owner,
                       t.exact_sha, t.updated_at, w.product, w.section,
                       w.subsection, w.branch, w.worktree, w.base_sha
                FROM atomic_tasks t
                JOIN workstations w ON t.workstation_id = w.workstation_id
                WHERE w.product = %s ORDER BY t.updated_at DESC LIMIT 200
            """,
                (repository,),
            )
            rows = c.fetchall()
        return [
            {
                "task_id": r["task_id"],
                "repository": r["product"],
                "area": r["section"],
                "sub_area": r["subsection"],
                "title": r["title"],
                "status": r["status"],
                "owner": r["owner"],
                "exact_sha": r["exact_sha"],
                "updated_at": self._text(r, "updated_at"),
                "completion_percent": 100 if r["status"] == "CERTIFIED" else 0,
                "execution_ready": r["status"] == "CERTIFIED",
                "execution_contract": {
                    "worktree": r["worktree"],
                    "branch": r["branch"],
                    "base_sha": r["base_sha"],
                    "stage": "REVIEW" if r["status"] == "CERTIFIED" else "IMPLEMENTATION",
                },
            }
            for r in rows
        ]

    def task(self, task_id: str):
        with self.connection() as c:
            c.execute(
                """
                SELECT w.product FROM atomic_tasks t
                JOIN workstations w ON w.workstation_id = t.workstation_id
                WHERE t.task_id=%s LIMIT 1
            """,
                (task_id,),
            )
            row = c.fetchone()
        if row is None:
            return None
        return next((x for x in self.tasks(row["product"]) if x["task_id"] == task_id), None)

    def local_work(self, repository: str, recent_hours: int):
        if not 1 <= recent_hours <= 720:
            raise ValueError("recent_hours_invalid")
        with self.connection() as c:
            c.execute(
                """
                SELECT workstation_id, branch, worktree, local_sha, remote_sha,
                       status, blocker, heartbeat_at, updated_at
                FROM workstations WHERE product=%s
                ORDER BY order_index LIMIT 200
            """,
                (repository,),
            )
            rows = c.fetchall()
        return {
            "repository": repository,
            "source": "postgres_workstation_registry",
            "lanes": [
                {
                    "worktree": r["worktree"],
                    "branch": r["branch"],
                    "head": r["local_sha"],
                    "remote_head": r["remote_sha"],
                    # Dirty/conflicts cannot be inferred from PostgreSQL alone.
                    "classification": "UNKNOWN_LOCAL_STATE",
                    "reason": r["blocker"] or "Worktree must be inspected on its host",
                    "status": r["status"],
                    "workstation_id": r["workstation_id"],
                    "updated_at": self._text(r, "updated_at"),
                }
                for r in rows
            ],
        }
