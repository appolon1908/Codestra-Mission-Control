import json,sqlite3
from mission_control.store import MissionStore
from mission_control.work_authority import WorkAuthority,WorkItem,WorkType
repos=json.loads(r'''["Kong","OpenAPI","Agent-Desktop-WebRTC","Mobile-Android-Agent","OBS-Studio-Nabeel-","Wazuh","codestra-backend","Codestra-Alertmanager","Breero.com","Mobile-Control-UI","LARIM-A-Backend","Mobile-Device-Gateway","Codestra-Face-Liveness","Codestra-Connect","booked4seasons","Codestra-TTS-Piper","Codestra-Loki","Codestra-Postgres-Exporter","Owncast-Nabeel","Vicidialer-Codestra","Codestra-Redis-Exporter","Caddy","Meltano","N8N","Mobile-Control-Console","codestra-provisioning-service","chatbox","Superset","Codestra-cAdvisor","Codestra-Node-Exporter","beyvra-frontend","FACE-ID","Codestra-Camera-Gateway","Mobile-Sync","Odoo","Evolution-API","openrerefine-","Codestra-TTS-Kokoro","Codestra-Device-Forge","Codestra-Document-Intelligence","beyvra-backend","Moneybee-Backend","klyrow-Website-","Codestra-Workstation","codestra-production-platform","WhatsApp-Frontend","Infustruction-repo","codestra-platform","Leads-Workstation","Codestra-Mission-Control","codestra-server-c","Codestra-Alloy","Moneybee-frontend-","kyqra-crawler","Codestra-Grafana-","Codestra-Document-SDK","Codestra-OpenBao","WhatsApp","MediaMTX","codestra-foundation","Codestra-Mixxx-Native","backend2","Codestra-Marketing-","scrapper","DJONE","Codestra-OCR-Workers","Codesrea-Social-","codestra-ruleset-toolkit","Codestra-Telemetry","Keycloak","InsightFace","Codestra-Tempo","Codestra-Communication-CC","Codestra-Prometheus","Codestra-TTS-F5","Mobile-Contracts-SDK","Middleware-","kyqra","Codestra-Document-Schemas","SDK-repository","Telnexa-web","Sentry","communication-platform-","Codestra-AI","Mobile-Control-Server","Codestra-Blackbox-Exporter","social.codestra.co","codestra","Chiaki","Meltano-Leads-Importer","Codestra-PostgreSQL","telnexa","klyrow.com","kyyow-contracts","Codestra-Document-Console","websocket","Codestra-Voice-Agent","Codestra-Lead-importer-api","Database-migrations-"]''')
db="/home/codestra/Worktrees/Runtime-Agent-Brain/agent-brain.db"
s=MissionStore(db);s.initialize();w=WorkAuthority(s);w.initialize()
with s.connection() as c:
 c.execute("""create table if not exists repository_registry(
 repository text primary key, full_name text not null, source text not null,
 status text not null default 'DISCOVERED', mission_state text not null default 'UNPLANNED')""")
 for repo in repos:
  c.execute("""insert into repository_registry(repository,full_name,source,status,mission_state)
   values(?,?,'GitHub','DISCOVERED','UNPLANNED')
   on conflict(repository) do update set full_name=excluded.full_name,source='GitHub'""",(repo,"ingtrader21-spec/"+repo))
  mid="PORTFOLIO-"+repo
  w.publish(WorkItem(mid,repo,mid,WorkType.MISSION,repo+" development portfolio",metadata={"discovery":"github","planning_required":True}))
print("registered",len(repos),"repositories")
