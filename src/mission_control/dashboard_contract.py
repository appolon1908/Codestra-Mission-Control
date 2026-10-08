from __future__ import annotations

DASHBOARD_ENDPOINTS={
 "repositories":{"method":"GET","path":"/platform/v1/dashboard/repositories","ui":"Repositories table/count/status/progress"},
 "openbao":{"method":"GET","path":"/platform/v1/dashboard/openbao","ui":"OpenBao read-only reconciler status, PR summary, mission workstreams; requires mission:read"},
 "sources":{"method":"GET","path":"/platform/v1/dashboard/sources","ui":"Data source authority"},
 "repository":{"method":"GET","path":"/platform/v1/dashboard/repository?repository={repository}","ui":"Repository mission/PR detail"},
 "agents":{"method":"GET","path":"/platform/v1/dashboard/agents","ui":"Live agent lanes"},
 "notifications":{"method":"GET","path":"/platform/v1/dashboard/notifications","ui":"Notifications/activity"},
 "assignment_claim":{"method":"POST","path":"/platform/v1/assignments","ui":"Claim task"},
 "router_snapshot":{"method":"GET","path":"/platform/v1/mission-router/snapshot?repository={repository}","ui":"Sections/subsections/tasks/progress"},
 "router_next":{"method":"GET","path":"/platform/v1/mission-router/next?repository={repository}&agent_id={agent_id}","ui":"Next eligible task"},
 "watchdog_snapshot":{"method":"GET","path":"/platform/v1/watchdog/snapshot","ui":"Runtime health/leases/blockers"},
 "worker_nodes":{"method":"GET","path":"/platform/v1/worker-nodes","ui":"Workstations/agents"},
}
def dashboard_contract():return {"version":"v1","endpoints":DASHBOARD_ENDPOINTS}
