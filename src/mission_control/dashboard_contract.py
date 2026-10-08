from __future__ import annotations

DASHBOARD_ENDPOINTS={
 "repositories":{"method":"GET","path":"/platform/v1/dashboard/repositories","ui":"Repositories table/count/status/progress"},
 "tasks":{"method":"GET","path":"/platform/v1/dashboard/tasks?repository={repository}","ui":"Verified atomic task rows from PostgreSQL"},
 "task":{"method":"GET","path":"/platform/v1/dashboard/task?task_id={task_id}","ui":"Single verified task detail"},
 "local_work":{"method":"GET","path":"/platform/v1/dashboard/local-work?repository={repository}&recent_hours=48","ui":"Registered worktrees; dirty state explicitly UNKNOWN"},
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
