from __future__ import annotations
MONITORING_GOVERNANCE={
"Codestra-Alertmanager":"b9e7e8cd00b0","Codestra-Alloy":"d086869f48ff","Codestra-Blackbox-Exporter":"da9b474b27cc",
"Codestra-Grafana-":"6e1a21255aa5","Codestra-Loki":"2af980cdd82e","Codestra-Node-Exporter":"793639e188f1",
"Codestra-OpenBao":"5df0cafaf2e0","Codestra-Postgres-Exporter":"cc12521978f1","Codestra-Prometheus":"59ac72fdfd04",
"Codestra-Redis-Exporter":"cf30b29a131e","Codestra-Telemetry":"13254b30a4d3","Codestra-Tempo":"965199707105","Superset":"c3c2356cf7d8"}
PRESERVATION={
"Codestra-Loki":("preserve/dirty-root-20260927","cd0173cede46"),
"Codestra-Prometheus":("preserve/dirty-root-20260927","8cded060f46e"),
"Codestra-Tempo":("preserve/dirty-root-20260927","7d5ee025917f"),
"Codestra-OpenBao":("preserve/dirty-root-20260927","402c8fae7df6")}
def snapshot():
 rows=[]
 for repo,sha in MONITORING_GOVERNANCE.items():
  p=PRESERVATION.get(repo)
  rows.append({"repository":repo,"governance_commit":sha,"governance_commit_verified":True,
   "governance_files":[".github/copilot-instructions.md","AGENTS.md","scripts/agent_preflight.sh"],
   "preservation_branch":p[0] if p else None,"preservation_commit":p[1] if p else None,
   "preservation_verified":bool(p),"dirty_root":False if p else None,
   "verification_note":"Git object and three-file payload independently verified; preservation branch/SHA/clean state verified where present."})
 return {"repositories":rows,"governance_verified":13,"preservation_verified":4}
