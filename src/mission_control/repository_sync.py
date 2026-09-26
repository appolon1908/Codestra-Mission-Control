from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RepoSnapshot:
 repository:str; full_name:str; default_branch:str; remote_head:str|None
 archived:bool=False; visibility:str="private"

class RepositorySync:
 def __init__(self,store):self.store=store
 def initialize(self):
  with self.store.connection() as c:c.executescript("""
  CREATE TABLE IF NOT EXISTS repository_registry(
   repository TEXT PRIMARY KEY,full_name TEXT NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'DISCOVERED',
   mission_state TEXT NOT NULL DEFAULT 'UNPLANNED',default_branch TEXT,remote_head TEXT,archived INTEGER NOT NULL DEFAULT 0,
   visibility TEXT,last_synced_at TEXT);
  CREATE TABLE IF NOT EXISTS repository_sync_events(
   id INTEGER PRIMARY KEY AUTOINCREMENT,repository TEXT NOT NULL,event TEXT NOT NULL,old_value TEXT,new_value TEXT,
   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
  """)
 def apply(self,s:RepoSnapshot):
  with self.store.connection() as c:
   old=c.execute("select * from repository_registry where repository=?",(s.repository,)).fetchone()
   if old and old["remote_head"] and s.remote_head and old["remote_head"]!=s.remote_head:
    c.execute("insert into repository_sync_events(repository,event,old_value,new_value) values(?,?,?,?)",
              (s.repository,"REMOTE_HEAD_CHANGED",old["remote_head"],s.remote_head))
   c.execute("""insert into repository_registry(repository,full_name,source,status,mission_state,default_branch,remote_head,archived,visibility,last_synced_at)
    values(?,?,'GitHub','SYNCED','UNPLANNED',?,?,?,?,CURRENT_TIMESTAMP)
    on conflict(repository) do update set full_name=excluded.full_name,status='SYNCED',default_branch=excluded.default_branch,
    remote_head=excluded.remote_head,archived=excluded.archived,visibility=excluded.visibility,last_synced_at=CURRENT_TIMESTAMP""",
    (s.repository,s.full_name,s.default_branch,s.remote_head,int(s.archived),s.visibility))
 def mark_missing(self,present:set[str]):
  with self.store.connection() as c:
   for r in c.execute("select repository from repository_registry").fetchall():
    if r["repository"] not in present:c.execute("update repository_registry set status='MISSING_REMOTE',last_synced_at=CURRENT_TIMESTAMP where repository=?",(r["repository"],))
