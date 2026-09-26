from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class MissionCharter:
    mission_id: str
    repository: str
    title: str
    goal: str
    definition_of_done: tuple[str, ...]
    architecture: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    supersedes: str | None = None
    version: int = 1

    @property
    def digest(self) -> str:
        payload=json.dumps(asdict(self),sort_keys=True,separators=(",",":")).encode()
        return hashlib.sha256(payload).hexdigest()


class MissionArchitect:
    def __init__(self, store) -> None:
        self.store=store

    def initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS mission_charters (
                mission_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                repository TEXT NOT NULL,
                title TEXT NOT NULL,
                charter_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(mission_id,version)
            );
            CREATE TABLE IF NOT EXISTS mission_acknowledgements (
                mission_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                agent_id TEXT NOT NULL,
                digest TEXT NOT NULL,
                acknowledged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(mission_id,version,agent_id)
            );
            """)

    def publish(self, charter: MissionCharter) -> str:
        with self.store.connection() as conn:
            conn.execute("UPDATE mission_charters SET active=0 WHERE mission_id=?", (charter.mission_id,))
            conn.execute("""INSERT INTO mission_charters
                (mission_id,version,repository,title,charter_json,digest,active)
                VALUES(?,?,?,?,?,?,1)""",
                (charter.mission_id,charter.version,charter.repository,charter.title,
                 json.dumps(asdict(charter),sort_keys=True),charter.digest))
        return charter.digest

    def acknowledge(self, mission_id: str, version: int, agent_id: str, digest: str) -> None:
        with self.store.connection() as conn:
            row=conn.execute("SELECT digest FROM mission_charters WHERE mission_id=? AND version=? AND active=1",
                             (mission_id,version)).fetchone()
            if not row or row["digest"] != digest:
                raise ValueError("mission charter is stale or digest does not match")
            conn.execute("""INSERT INTO mission_acknowledgements(mission_id,version,agent_id,digest)
                VALUES(?,?,?,?) ON CONFLICT(mission_id,version,agent_id)
                DO UPDATE SET digest=excluded.digest,acknowledged_at=CURRENT_TIMESTAMP""",
                (mission_id,version,agent_id,digest))

    def agent_context(self, mission_id: str, agent_id: str) -> dict:
        with self.store.connection() as conn:
            row=conn.execute("SELECT * FROM mission_charters WHERE mission_id=? AND active=1 ORDER BY version DESC LIMIT 1",
                             (mission_id,)).fetchone()
            if not row: raise KeyError(mission_id)
            ack=conn.execute("SELECT digest FROM mission_acknowledgements WHERE mission_id=? AND version=? AND agent_id=?",
                             (mission_id,row["version"],agent_id)).fetchone()
        charter=json.loads(row["charter_json"])
        return {"mission":charter,"digest":row["digest"],"acknowledged":bool(ack and ack["digest"]==row["digest"])}
