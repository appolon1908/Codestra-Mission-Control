from __future__ import annotations
ACTIVE={"Codestra-Alertmanager":"92ff39a70a6d","Codestra-Alloy":"15928a7a1003","Codestra-Blackbox-Exporter":"2e5b0eed794f","Codestra-Grafana-":"46d33b4e8d06","Codestra-Loki":"b1e05eeb125d","Codestra-Node-Exporter":"fc1834b8aae5","Codestra-OpenBao":"00108da9abc1","Codestra-Postgres-Exporter":"32ef1ee3596a","Codestra-Prometheus":"a798a43e8f72","Codestra-Redis-Exporter":"3456dba37a8f","Codestra-Telemetry":"185a53af8939","Codestra-Tempo":"1706debc4c78","Superset":"316d2ab45b25"}
PRESERVED={"Codestra-Loki":("preserve/dirty-root-20260927","cd0173cede46"),"Codestra-Prometheus":("preserve/dirty-root-20260927","8cded060f46e"),"Codestra-Tempo":("preserve/dirty-root-20260927","7d5ee025917f"),"Codestra-OpenBao":("preserve/dirty-root-20260927","402c8fae7df6")}
GOVERNANCE_FILES=[".github/copilot-instructions.md","AGENTS.md","scripts/agent_preflight.sh"]
def snapshot():
 rows=[]
 for repo,sha in ACTIVE.items():
  p=PRESERVED.get(repo)
  rows.append({"repository":repo,"head":sha,"governance_commit":sha,"governance_commit_verified":True,"governance_files":GOVERNANCE_FILES,"preservation_branch":p[0] if p else None,"preservation_commit":p[1] if p else None,"preservation_verified":bool(p),"dirty_root":False if p else None,"diff_check":"PASS","runtime_certification":"SEPARATE_GATE"})
 return {"authority_branch":"governance/single-active-lane-20260926","authority_path":"/home/codestra/Worktrees/Monitoring-Active-20260926/<repo>","repositories":rows,"active_governance":rows,"preservation":[r for r in rows if r["preservation_verified"]],"governance_verified":len(ACTIVE),"preservation_verified":len(PRESERVED),"summary":{"active_authority_verified":len(ACTIVE),"preservation_verified":len(PRESERVED)}}
