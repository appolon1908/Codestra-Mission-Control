from mission_control.store import MissionStore
from mission_control.mission_graph import MissionGraphStore
from mission_control.router_store import RouterStore
from mission_control.mission_router import AtomicTask

DB="/home/codestra/Worktrees/Runtime-Agent-Brain/agent-brain.db"
s=MissionStore(DB);s.initialize()
g=MissionGraphStore(s);g.initialize()
r=RouterStore(s);r.initialize()
r.upsert_area("WhatsApp","API","API",1)
r.upsert_subarea("WhatsApp","API","AI-Drafts","AI Drafts",1)
r.upsert_task(AtomicTask("WA-AI-DRAFTS-COMPLETE","WhatsApp","API","AI-Drafts","WHATSAPP-AI-DRAFTS-COMPLETE",priority=10,required_skills=frozenset({"review","api","testing"}),collision_keys=frozenset({"whatsapp-ai-drafts","pr-12"})))

g.add_node("wa-area-api","WhatsApp","AREA","API",sequence=1)
g.add_node("wa-sub-ai-drafts","WhatsApp","SUBAREA","AI Drafts","wa-area-api",sequence=1)
g.add_node("wa-feature-ai-draft-api","WhatsApp","FEATURE","Governed AI Draft API","wa-sub-ai-drafts",sequence=1,
           metadata={"pr":12,"goal":"Finish the AI Drafts section to certified completion","production_effects":False})
g.add_node("WA-AI-DRAFTS-COMPLETE","WhatsApp","ATOMIC_TASK","Finish AI Drafts section","wa-feature-ai-draft-api",sequence=1,
           metadata={"pr":12,"required_flow":["review","fix","testing","postman","openapi","ci","certification"],
                     "definition_of_done":["PR implementation reviewed","findings fixed","API contract verified","Postman tests green","backend tests green","CI evidence green","no provider effects","ready for governed merge"]})
print("registered WhatsApp/API/AI-Drafts/WA-AI-DRAFTS-COMPLETE")
