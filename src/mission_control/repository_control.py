from __future__ import annotations

class RepositoryControlCenter:
    def __init__(self, store): self.store=store
    def rows(self):
        with self.store.connection() as c:
            repos=c.execute("SELECT * FROM repository_registry ORDER BY repository COLLATE NOCASE").fetchall()
            states={r["repository"]:dict(r) for r in c.execute("SELECT * FROM repository_state").fetchall()}
            delivery={r["repository"]:dict(r) for r in c.execute("SELECT * FROM repository_delivery_state").fetchall()}
            agents={r["repository"]:r["n"] for r in c.execute(
                "SELECT repository,count(*) n FROM agent_lane_presence WHERE repository IS NOT NULL GROUP BY repository").fetchall()}
            tasks={r["repository"]:dict(r) for r in c.execute(
                """SELECT repository,count(*) total,
                sum(CASE WHEN certified=1 THEN 1 ELSE 0 END) certified,
                avg(completion_percent) wip FROM atomic_tasks GROUP BY repository""").fetchall()}
        rows=[]
        for repo in repos:
            name=repo["repository"]; state=states.get(name,{}); work=tasks.get(name,{}); ship=delivery.get(name,{})
            rows.append({
                "repository":name,"full_name":repo["full_name"],"planning":repo["mission_state"],
                "sync_state":state.get("sync_state","UNKNOWN"),"open_prs":state.get("open_prs",0) or 0,
                "ci_state":state.get("ci_state","UNKNOWN"),"active_agents":agents.get(name,0),
                "wip_percent":round(work.get("wip") or 0,1),
                "certified_tasks":work.get("certified",0) or 0,"total_tasks":work.get("total",0) or 0,
                "remote_head_sha":state.get("remote_head_sha"),"last_synced_at":state.get("last_synced_at"),
                "sync_source":"Git/GitHub reconciler","pr_source":"GitHub PR reconciler","ci_source":"GitHub CI/checks",
                "agent_source":"Agent Brain heartbeat registry","progress_source":"Mission Router tasks/evidence",
                "api_source":"OpenAPI + API catalog",
                "ci_defined":bool(ship.get("ci_defined",0)),"ci_connected":bool(ship.get("ci_connected",0)),
                "ci_healthy":ship.get("ci_healthy"),"cd_defined":bool(ship.get("cd_defined",0)),
                "production_locked":bool(ship.get("production_locked",1)),"last_pr_number":ship.get("last_pr_number"),
                "last_pr_title":ship.get("last_pr_title"),"last_pr_head_sha":ship.get("last_pr_head_sha"),
                "last_pr_pushed_at":ship.get("last_pr_pushed_at"),"last_pr_updated_at":ship.get("last_pr_updated_at"),
            })
        return rows
