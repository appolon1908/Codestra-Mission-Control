from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum


class WorkState(StrEnum):
 PLANNED="PLANNED"; READY="READY"; IMPLEMENTING="IMPLEMENTING"; IMPLEMENTED="IMPLEMENTED"
 REVIEWING="REVIEWING"; REPAIR="REPAIR"; TESTING="TESTING"; CERTIFYING="CERTIFYING"
 MERGE_READY="MERGE_READY"; MERGED="MERGED"; VERIFIED="VERIFIED"; CERTIFIED="CERTIFIED"

class WorkType(StrEnum):
 MISSION="MISSION"; AREA="AREA"; SUBAREA="SUBAREA"; DELIVERABLE="DELIVERABLE"; ATOMIC_TASK="ATOMIC_TASK"

@dataclass(frozen=True)
class WorkItem:
 work_id:str; repository:str; mission_id:str; work_type:WorkType; title:str
 parent_id:str|None=None; state:WorkState=WorkState.PLANNED; revision:int=1
 metadata:dict|None=None

class WorkAuthority:
 def __init__(self,store):self.store=store
 def initialize(self):
  with self.store.connection() as c:c.executescript("""
  CREATE TABLE IF NOT EXISTS work_items(
   work_id TEXT PRIMARY KEY,repository TEXT NOT NULL,mission_id TEXT NOT NULL,work_type TEXT NOT NULL,
   title TEXT NOT NULL,parent_id TEXT,state TEXT NOT NULL,revision INTEGER NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}');
  CREATE TABLE IF NOT EXISTS work_leases(
   lease_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,agent_id TEXT NOT NULL,lane TEXT NOT NULL,state TEXT NOT NULL,
   branch TEXT,worktree TEXT,base_sha TEXT,expires_at TEXT NOT NULL,
   FOREIGN KEY(work_id) REFERENCES work_items(work_id));
  CREATE TABLE IF NOT EXISTS work_evidence(
   evidence_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,evidence_type TEXT NOT NULL,status TEXT NOT NULL,
   reference TEXT,digest TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',
   FOREIGN KEY(work_id) REFERENCES work_items(work_id));
  CREATE TABLE IF NOT EXISTS work_pr_bindings(
   work_id TEXT NOT NULL,repository TEXT NOT NULL,pr_number INTEGER NOT NULL,
   PRIMARY KEY(work_id,repository,pr_number),FOREIGN KEY(work_id) REFERENCES work_items(work_id));
  """)
 def publish(self,item:WorkItem):
  with self.store.connection() as c:
   if item.parent_id and not c.execute("select 1 from work_items where work_id=?",(item.parent_id,)).fetchone():raise ValueError("parent_work_item_missing")
   c.execute("""insert into work_items values(?,?,?,?,?,?,?,?,?)
    on conflict(work_id) do update set title=excluded.title,state=excluded.state,revision=excluded.revision,metadata_json=excluded.metadata_json""",
    (item.work_id,item.repository,item.mission_id,item.work_type.value,item.title,item.parent_id,item.state.value,item.revision,json.dumps(item.metadata or {},sort_keys=True)))
 def require_work(self,work_id):
  with self.store.connection() as c:r=c.execute("select * from work_items where work_id=?",(work_id,)).fetchone()
  if not r:raise ValueError("canonical_work_item_missing")
  return dict(r)
 def validate_implementation_start(self,work_id,*,branch,worktree,base_sha):
  self.require_work(work_id)
  if not branch or branch in {"main","master"}:raise ValueError("governed_branch_required")
  if not worktree:raise ValueError("governed_worktree_required")
  if not base_sha:raise ValueError("base_sha_required")
 def bind_pr(self,work_id,repository,pr_number):
  self.require_work(work_id)
  with self.store.connection() as c:c.execute("insert or ignore into work_pr_bindings values(?,?,?)",(work_id,repository,pr_number))
 def certification_ready(self,work_id,required):
  self.require_work(work_id)
  with self.store.connection() as c:green={r["evidence_type"] for r in c.execute("select evidence_type from work_evidence where work_id=? and status='GREEN'",(work_id,))}
  return set(required)<=green
