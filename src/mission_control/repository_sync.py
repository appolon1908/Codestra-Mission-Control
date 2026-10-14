from __future__ import annotations

from datetime import UTC, datetime


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
  CREATE TABLE IF NOT EXISTS repository_delivery_state(
   repository TEXT PRIMARY KEY,ci_defined INTEGER NOT NULL DEFAULT 0,ci_connected INTEGER NOT NULL DEFAULT 0,
   ci_healthy INTEGER,cd_defined INTEGER NOT NULL DEFAULT 0,production_locked INTEGER NOT NULL DEFAULT 1,
   last_pr_number INTEGER,last_pr_title TEXT,last_pr_head_sha TEXT,last_pr_pushed_at TEXT,last_pr_updated_at TEXT,
   last_checked_at TEXT NOT NULL);
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

 def upsert_delivery(self,repository,**x):
  now=datetime.now(UTC).isoformat()
  with self.store.connection() as c:c.execute("""insert into repository_delivery_state values(?,?,?,?,?,?,?,?,?,?,?,?)
   on conflict(repository) do update set ci_defined=excluded.ci_defined,ci_connected=excluded.ci_connected,
   ci_healthy=excluded.ci_healthy,cd_defined=excluded.cd_defined,production_locked=excluded.production_locked,
   last_pr_number=excluded.last_pr_number,last_pr_title=excluded.last_pr_title,last_pr_head_sha=excluded.last_pr_head_sha,
   last_pr_pushed_at=excluded.last_pr_pushed_at,last_pr_updated_at=excluded.last_pr_updated_at,last_checked_at=excluded.last_checked_at""",
   (repository,int(x.get("ci_defined",False)),int(x.get("ci_connected",False)),x.get("ci_healthy"),int(x.get("cd_defined",False)),
    int(x.get("production_locked",True)),x.get("last_pr_number"),x.get("last_pr_title"),x.get("last_pr_head_sha"),
    x.get("last_pr_pushed_at"),x.get("last_pr_updated_at"),now))
