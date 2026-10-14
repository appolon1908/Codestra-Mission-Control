from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Claim:
    task_id:str; agent_id:str; repository:str; area:str; subarea:str
    lease_id:str; expires_at:str

class AssignmentStore:
    def __init__(self,store): self.store=store
    def initialize(self):
        with self.store.connection() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS task_claims(
              task_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,repository TEXT NOT NULL,
              area TEXT NOT NULL,subarea TEXT NOT NULL,lease_id TEXT NOT NULL UNIQUE,
              claimed_at TEXT NOT NULL,expires_at TEXT NOT NULL,state TEXT NOT NULL);
            """)
    def claim(self,*,task_id,agent_id,repository,area,subarea,lease_minutes=30)->Claim:
        now=datetime.now(UTC);expires=now+timedelta(minutes=lease_minutes);lease=secrets.token_urlsafe(18)
        with self.store.connection() as c:
            agent=c.execute("SELECT enabled FROM agent_registry WHERE agent_id=?",(agent_id,)).fetchone()
            if not agent or not agent["enabled"]: raise ValueError("agent_not_registered_or_disabled")
            existing=c.execute("SELECT * FROM task_claims WHERE task_id=? AND state='ACTIVE'",(task_id,)).fetchone()
            if existing and datetime.fromisoformat(existing["expires_at"])>now: raise ValueError("task_already_claimed")
            c.execute("""INSERT INTO task_claims VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(task_id) DO UPDATE SET agent_id=excluded.agent_id,repository=excluded.repository,
              area=excluded.area,subarea=excluded.subarea,lease_id=excluded.lease_id,
              claimed_at=excluded.claimed_at,expires_at=excluded.expires_at,state='ACTIVE'""",
              (task_id,agent_id,repository,area,subarea,lease,now.isoformat(),expires.isoformat(),"ACTIVE"))
        return Claim(task_id,agent_id,repository,area,subarea,lease,expires.isoformat())
