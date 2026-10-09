from __future__ import annotations

import json


class MissionGraphStore:
    def __init__(self,store): self.store=store
    def initialize(self):
        with self.store.connection() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS mission_graph_nodes(
              node_id TEXT PRIMARY KEY,repository TEXT NOT NULL,node_type TEXT NOT NULL,
              parent_id TEXT,title TEXT NOT NULL,sequence INTEGER NOT NULL DEFAULT 0,
              metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS mission_graph_edges(
              source_id TEXT NOT NULL,target_id TEXT NOT NULL,edge_type TEXT NOT NULL,
              PRIMARY KEY(source_id,target_id,edge_type));
            CREATE TABLE IF NOT EXISTS pull_requests(
              repository TEXT NOT NULL,pr_number INTEGER NOT NULL,head_sha TEXT NOT NULL,
              base_sha TEXT NOT NULL,branch TEXT NOT NULL,state TEXT NOT NULL,
              implementation_agent TEXT,review_agent TEXT,test_agent TEXT,
              ci_state TEXT NOT NULL DEFAULT 'UNKNOWN',merge_state TEXT NOT NULL DEFAULT 'UNMERGED',
              post_merge_verified INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(repository,pr_number));
            CREATE TABLE IF NOT EXISTS pr_task_bindings(
              repository TEXT NOT NULL,pr_number INTEGER NOT NULL,task_id TEXT NOT NULL,
              area_id TEXT,subarea_id TEXT,feature_id TEXT,api_contract TEXT,
              PRIMARY KEY(repository,pr_number,task_id));
            """)
    def add_node(self,node_id,repository,node_type,title,parent_id=None,sequence=0,metadata=None):
        with self.store.connection() as c:
            c.execute("""INSERT INTO mission_graph_nodes VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET parent_id=excluded.parent_id,title=excluded.title,
            sequence=excluded.sequence,metadata_json=excluded.metadata_json""",
            (node_id,repository,node_type,parent_id,title,sequence,json.dumps(metadata or {},sort_keys=True)))
    def add_edge(self,source,target,edge_type="DEPENDS_ON"):
        with self.store.connection() as c:
            c.execute("INSERT OR IGNORE INTO mission_graph_edges VALUES(?,?,?)",(source,target,edge_type))
    def upsert_pr(self,repository,number,head_sha,base_sha,branch,state,**kwargs):
        with self.store.connection() as c:
            c.execute("""INSERT INTO pull_requests(repository,pr_number,head_sha,base_sha,branch,state,
            implementation_agent,review_agent,test_agent,ci_state,merge_state,post_merge_verified)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(repository,pr_number) DO UPDATE SET head_sha=excluded.head_sha,base_sha=excluded.base_sha,
            branch=excluded.branch,state=excluded.state,implementation_agent=excluded.implementation_agent,
            review_agent=excluded.review_agent,test_agent=excluded.test_agent,ci_state=excluded.ci_state,
            merge_state=excluded.merge_state,post_merge_verified=excluded.post_merge_verified,
            updated_at=CURRENT_TIMESTAMP""",
            (repository,number,head_sha,base_sha,branch,state,kwargs.get("implementation_agent"),
             kwargs.get("review_agent"),kwargs.get("test_agent"),kwargs.get("ci_state","UNKNOWN"),
             kwargs.get("merge_state","UNMERGED"),int(kwargs.get("post_merge_verified",False))))
    def bind_pr_task(self,repository,number,task_id,area_id=None,subarea_id=None,feature_id=None,api_contract=None):
        with self.store.connection() as c:
            c.execute("INSERT OR REPLACE INTO pr_task_bindings VALUES(?,?,?,?,?,?,?)",
                      (repository,number,task_id,area_id,subarea_id,feature_id,api_contract))
    def task_prs(self,task_id):
        with self.store.connection() as c:
            return [dict(r) for r in c.execute("""SELECT p.*,b.area_id,b.subarea_id,b.feature_id,b.api_contract
            FROM pull_requests p JOIN pr_task_bindings b USING(repository,pr_number)
            WHERE b.task_id=? ORDER BY p.pr_number""",(task_id,)).fetchall()]
