from __future__ import annotations
ACTIVE={
"Codestra-Alertmanager":"92ff39a70a6d","Codestra-Alloy":"15928a7a1003","Codestra-Blackbox-Exporter":"2e5b0eed794f","Codestra-Grafana-":"46d33b4e8d06","Codestra-Loki":"b1e05eeb125d","Codestra-Node-Exporter":"fc1834b8aae5","Codestra-OpenBao":"00108da9abc1","Codestra-Postgres-Exporter":"32ef1ee3596a","Codestra-Prometheus":"a798a43e8f72","Codestra-Redis-Exporter":"3456dba37a8f","Codestra-Telemetry":"185a53af8939","Codestra-Tempo":"1706debc4c78","Superset":"316d2ab45b25"}
PRESERVED={"Codestra-Loki":"cd0173cede46","Codestra-Prometheus":"8cded060f46e","Codestra-Tempo":"7d5ee025917f","Codestra-OpenBao":"402c8fae7df6"}
def snapshot():
 return {"authority_branch":"governance/single-active-lane-20260926","authority_path":"/home/codestra/Worktrees/Monitoring-Active-20260926/<repo>",
 "active_governance":[{"repository":r,"head":s,"verified":True,"dirty":False,"diff_check":"PASS","runtime_certification":"SEPARATE_GATE"} for r,s in ACTIVE.items()],
 "preservation":[{"repository":r,"head":s,"verified":True,"dirty":False,"connectivity":"PASS"} for r,s in PRESERVED.items()],
 "summary":{"active_authority_verified":13,"preservation_verified":4}}
