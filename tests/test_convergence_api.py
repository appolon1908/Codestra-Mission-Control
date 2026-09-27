import json,threading
from urllib.request import Request,urlopen
from mission_control.dashboard_api import DashboardAPI
from mission_control.store import MissionStore
from mission_control.agent_registry import AgentRegistry
from mission_control.oversight import OversightStore
from mission_control.mission_graph import MissionGraphStore
from mission_control.router_store import RouterStore
from mission_control.mission_router import AtomicTask

def setup(tmp_path,monkeypatch):
 monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE","disabled")
 s=MissionStore(tmp_path/"db");s.initialize();AgentRegistry(s).initialize();OversightStore(s).initialize();MissionGraphStore(s).initialize();r=RouterStore(s);r.initialize()
 r.upsert_task(AtomicTask("T1","Repo","A","S","M",completion_percent=20,collision_keys=frozenset({"src/x"})))
 with s.connection() as c:c.execute("CREATE TABLE execution_contracts(task_id TEXT PRIMARY KEY,repository TEXT,stage TEXT,objective TEXT,worktree TEXT,branch TEXT,base_sha TEXT,writable_scope_json TEXT,readonly_dependencies_json TEXT,forbidden_scope_json TEXT,acceptance_json TEXT,evidence_json TEXT,api_operations_json TEXT,headers_json TEXT,revision INTEGER)");c.execute("INSERT INTO execution_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("T1","Repo","IMPLEMENTATION","code it","/w","mission/t1","a"*40,'["src/"]','[]','["main"]','["tests pass"]','["pytest"]','[]','[]',1))
 srv=DashboardAPI(s).server();threading.Thread(target=srv.serve_forever,daemon=True).start();return srv

def req(srv,path,method="GET",body=None):
 u=f"http://127.0.0.1:{srv.server_port}{path}";data=json.dumps(body).encode() if body is not None else None
 r=urlopen(Request(u,data=data,method=method,headers={"Content-Type":"application/json"}),timeout=5);return r.status,json.load(r)

def test_production_aliases_lease_heartbeat_and_certify(tmp_path,monkeypatch):
 srv=setup(tmp_path,monkeypatch)
 try:
  st,x=req(srv,"/api/v1/missions","POST",{"product_goal":"Converge","business_reason":"truth"});assert st==201 and x["mission_id"].startswith("MISSION-")
  st,x=req(srv,"/api/v1/tasks/T1/lease","POST",{"agent_id":"agent-1"});assert st==200 and x["base_sha"]=="a"*40 and x["collision_set"]==["src/x"]
  st,x=req(srv,"/api/v1/agents/heartbeat","POST",{"agent_id":"agent-1","task_id":"T1","lease_token":x["lease"]["lease_token"],"current_sha":"b"*40,"status":"ACTIVE","changed_files_count":1});assert st==200 and x["acknowledged"]
  st,x=req(srv,"/api/v1/certifications","POST",{"task_id":"T1","exact_sha":"b"*40,"test_pass_rate":100,"artifact_url":"file:///tmp/evidence"});assert st==201 and x["status"]=="CERTIFIED"
 finally:srv.shutdown();srv.server_close()
