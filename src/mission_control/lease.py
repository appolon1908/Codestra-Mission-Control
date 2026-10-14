
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import AgentRole, MissionStatus
from .store import MissionStore


class LeaseConflict(RuntimeError):
    pass


class LeaseNotOwned(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseResult:
    mission_id: str
    agent_id: str
    acquired: bool
    takeover: bool
    expires_at: str


def _now() -> datetime:
    return datetime.now(UTC)


class LeaseManager:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def claim(
        self,
        mission_id: str,
        agent_id: str,
        *,
        role: AgentRole = AgentRole.WRITER,
        ttl_seconds: int = 600,
    ) -> LeaseResult:
        if ttl_seconds < 30:
            raise ValueError("lease TTL must be at least 30 seconds")
        now = _now()
        expires = now + timedelta(seconds=ttl_seconds)

        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            mission = conn.execute(
                "SELECT status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not mission:
                conn.execute("ROLLBACK")
                raise KeyError(mission_id)

            existing = conn.execute(
                "SELECT * FROM leases WHERE mission_id=?",
                (mission_id,),
            ).fetchone()

            takeover = False
            if existing:
                current_expiry = datetime.fromisoformat(existing["expires_at"])
                if current_expiry > now and existing["agent_id"] != agent_id:
                    conn.execute("ROLLBACK")
                    raise LeaseConflict(
                        f"{mission_id} is leased by {existing['agent_id']} "
                        f"until {existing['expires_at']}"
                    )
                takeover = existing["agent_id"] != agent_id

            conn.execute(
                """
                INSERT INTO leases (
                    mission_id, agent_id, role, acquired_at, heartbeat_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    role=excluded.role,
                    acquired_at=excluded.acquired_at,
                    heartbeat_at=excluded.heartbeat_at,
                    expires_at=excluded.expires_at
                """,
                (
                    mission_id,
                    agent_id,
                    role.value,
                    now.isoformat(),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                (MissionStatus.WORKING.value, now.isoformat(), mission_id),
            )
            self.store._event(
                conn,
                mission_id,
                "LEASE_TAKEOVER" if takeover else "LEASE_CLAIMED",
                agent_id,
                {"role": role.value, "expires_at": expires.isoformat()},
            )
            conn.execute("COMMIT")

        return LeaseResult(
            mission_id=mission_id,
            agent_id=agent_id,
            acquired=True,
            takeover=takeover,
            expires_at=expires.isoformat(),
        )

    def heartbeat(
        self,
        mission_id: str,
        agent_id: str,
        *,
        ttl_seconds: int = 600,
    ) -> str:
        now = _now()
        expires = now + timedelta(seconds=ttl_seconds)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT * FROM leases WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not lease or lease["agent_id"] != agent_id:
                conn.execute("ROLLBACK")
                raise LeaseNotOwned(f"{agent_id} does not own {mission_id}")
            conn.execute(
                """
                UPDATE leases SET heartbeat_at=?, expires_at=?
                WHERE mission_id=? AND agent_id=?
                """,
                (now.isoformat(), expires.isoformat(), mission_id, agent_id),
            )
            self.store._event(
                conn,
                mission_id,
                "LEASE_HEARTBEAT",
                agent_id,
                {"expires_at": expires.isoformat()},
            )
            conn.execute("COMMIT")
        return expires.isoformat()

    def release(self, mission_id: str, agent_id: str, *, next_status: MissionStatus) -> None:
        if self.store.get_mission(mission_id):
            self.store.require_transition(mission_id, next_status)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT * FROM leases WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if not lease or lease["agent_id"] != agent_id:
                conn.execute("ROLLBACK")
                raise LeaseNotOwned(f"{agent_id} does not own {mission_id}")
            conn.execute("DELETE FROM leases WHERE mission_id=?", (mission_id,))
            conn.execute(
                "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                (next_status.value, _now().isoformat(), mission_id),
            )
            self.store._event(
                conn,
                mission_id,
                "LEASE_RELEASED",
                agent_id,
                {"next_status": next_status.value},
            )
            conn.execute("COMMIT")

    def expired_missions(self) -> list[dict]:
        now = _now().isoformat()
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM leases
                WHERE expires_at <= ?
                ORDER BY expires_at
                """,
                (now,),
            ).fetchall()
            return [dict(row) for row in rows]

    def is_owner(self, mission_id: str, agent_id: str) -> bool:
        """True only while ``agent_id`` holds an unexpired lease on the mission."""
        lease = self.current(mission_id)
        if not lease or lease["agent_id"] != agent_id:
            return False
        return datetime.fromisoformat(lease["expires_at"]) > _now()

    def current(self, mission_id: str) -> dict | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM leases WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            return dict(row) if row else None
