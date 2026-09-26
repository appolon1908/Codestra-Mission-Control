from __future__ import annotations

class RepositoryControlCenter:
    def __init__(self, store): self.store=store
    def rows(self):
        with self.store.connection() as c:
            repos=c.execute("SELECT * FROM repository_registry ORDER BY repository COLLATE NOCASE").fetchall()
            states={r["repository"]:dict(r) for r in c.execute("SELECT * FROM repository_state").fetchall()}
            agents={r["repository"]:r["n"] for r in c.execute(
                "SELECT repository,count(*) n FROM agent_lane_presence WHERE repository IS NOT NULL GROUP BY repository").fetchall()}
            tasks={r["repository"]:dict(r) for r in c.execute(
                """SELECT repository,count(*) total,
                sum(CASE WHEN certified=1 THEN 1 ELSE 0 END) certified,
                avg(completion_percent) wip FROM atomic_tasks GROUP BY repository""").fetchall()}
        rows=[]
        for repo in repos:
            name=repo["repository"]; state=states.get(name,{}); work=tasks.get(name,{})
            rows.append({
                "repository":name,"full_name":repo["full_name"],"planning":repo["mission_state"],
                "sync_state":state.get("sync_state","UNKNOWN"),"open_prs":state.get("open_prs",0) or 0,
                "ci_state":state.get("ci_state","UNKNOWN"),"active_agents":agents.get(name,0),
                "wip_percent":round(work.get("wip") or 0,1),
                "certified_tasks":work.get("certified",0) or 0,"total_tasks":work.get("total",0) or 0,
                "remote_head_sha":state.get("remote_head_sha"),"last_synced_at":state.get("last_synced_at"),
            })
        return rows
