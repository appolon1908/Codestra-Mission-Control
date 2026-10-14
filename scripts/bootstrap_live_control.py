from mission_control.store import MissionStore
from mission_control.agent_registry import AgentRegistry,AgentIdentity
from mission_control.router_store import RouterStore
from mission_control.mission_router import AtomicTask
from mission_control.mission_graph import MissionGraphStore
from mission_control.oversight import OversightStore,Supervisor
import argparse

p=argparse.ArgumentParser();p.add_argument("--db",required=True);a=p.parse_args()
s=MissionStore(a.db);s.initialize()
agents=AgentRegistry(s);agents.initialize()
router=RouterStore(s);router.initialize()
graph=MissionGraphStore(s);graph.initialize()
oversight=OversightStore(s);oversight.initialize()
identities=[
 AgentIdentity("codex-01","openai","codex","Codex Implementation",frozenset({"python","api","git"})),
 AgentIdentity("claude-01","anthropic","claude","Claude Review",frozenset({"review","security","architecture"})),
 AgentIdentity("api-01","openai","api","API Testing",frozenset({"api","postman","postgres"})),
 AgentIdentity("copilot-01","github","copilot","Copilot Implementation",frozenset({"frontend","typescript","git"})),
 AgentIdentity("grok-01","xai","grok","Grok Implementation",frozenset({"integration","api","testing"})),
]
for x in identities: agents.register(x)
lanes={
 "codex-01":("WORKING","Middleware-","CORE","Command Core","MW-CORE-01"),
 "claude-01":("REVIEWING","Middleware-","IDENTITY_SECURITY","JWT","MW-ID-01"),
 "api-01":("TESTING","Middleware-","API","Contracts","MW-API-01"),
 "copilot-01":("WORKING","codestra-platform","FEATURES","Mission Control","UI-01"),
 "grok-01":("WORKING","websocket","INTEGRATIONS_ADAPTERS","Realtime","WS-01"),
}
for aid,(state,repo,area,sub,task) in lanes.items():
 agents.heartbeat(aid,state=state,repository=repo,area_id=area,subarea_id=sub,mission_id="AGENT-BRAIN-V1",task_id=task,branch="governed/live",worktree="registered",workstation="UBUNTU_DESKTOP")
for i,(area,title) in enumerate([("CORE","Core"),("FEATURES","Features"),("API","APIs"),("URLS_ROUTES","URLs / Routes"),("WORKSTATIONS","Workstations"),("IDENTITY_SECURITY","Security"),("DATA_PERSISTENCE","Data"),("INTEGRATIONS_ADAPTERS","Adapters"),("OBSERVABILITY_AUDIT","Observability"),("DELIVERY_CI","Delivery / CI")],1):
 router.upsert_area("Middleware-",area,title,i)
 graph.add_node("area-"+area.lower(),"Middleware-","AREA",title,sequence=i)
oversight.register_supervisor(Supervisor("middleware-supervisor","REPOSITORY","Middleware-"))
oversight.register_supervisor(Supervisor("desktop-supervisor","WORKSTATION","UBUNTU_DESKTOP"))
print("registered",len(identities),"agents")
