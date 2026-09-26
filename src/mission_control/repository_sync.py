from __future__ import annotations
from datetime import UTC,datetime

class RepositorySyncStore:
 def __init__(self,store):self.store=store
 def initialize(self):
  with self.store.connection() as c:c.executescript("""
  CREATE TABLE IF NOT EXISTS repository_state(
   repository TEXT PRIMARY KEY,default_branch TEXT,remote_head_sha TEXT,local_head_sha TEXT,
   local_branch TEXT,dirty INTEGER NOT NULL DEFAULT 0,ahead INTEGER,behind INTEGER,
   open_prs INTEGER NOT NULL DEFAULT 0,ci_state TEXT NOT NULL DEFAULT 'UNKNOWN',
   sync_state TEXT NOT NULL DEFAULT 'UNKNOWN',last_synced_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS repository_pr_state(
   repository TEXT NOT NULL,pr_number INTEGER NOT NULL,title TEXT,state TEXT,head_sha TEXT,base_sha TEXT,
   draft INTEGER,mergeable TEXT,ci_state TEXT NOT NULL DEFAULT 'UNKNOWN',updated_at TEXT,
   PRIMARY KEY(repository,pr_number));
  CREATE TABLE IF NOT EXISTS repository_sync_events(
   id INTEGER PRIMARY KEY AUTOINCREMENT,repository TEXT NOT NULL,event_type TEXT NOT NULL,
   detail TEXT,created_at TEXT NOT NULL);
  """)
 def upsert_repo(self,repository,**state):
  now=datetime.now(UTC).isoformat()
  with self.store.connection() as c:c.execute("""insert into repository_state
   (repository,default_branch,remote_head_sha,local_head_sha,local_branch,dirty,ahead,behind,open_prs,ci_state,sync_state,last_synced_at)
   values(?,?,?,?,?,?,?,?,?,?,?,?)
   on conflict(repository) do update set default_branch=excluded.default_branch,remote_head_sha=excluded.remote_head_sha,
   local_head_sha=excluded.local_head_sha,local_branch=excluded.local_branch,dirty=excluded.dirty,ahead=excluded.ahead,
   behind=excluded.behind,open_prs=excluded.open_prs,ci_state=excluded.ci_state,sync_state=excluded.sync_state,last_synced_at=excluded.last_synced_at""",
   (repository,state.get("default_branch"),state.get("remote_head_sha"),state.get("local_head_sha"),state.get("local_branch"),
    int(state.get("dirty",False)),state.get("ahead"),state.get("behind"),state.get("open_prs",0),state.get("ci_state","UNKNOWN"),
    state.get("sync_state","UNKNOWN"),now))
 def snapshot(self,repository):
  with self.store.connection() as c:r=c.execute("select * from repository_state where repository=?",(repository,)).fetchone()
  return dict(r) if r else None
