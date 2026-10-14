from __future__ import annotations

from collections import defaultdict


class DashboardReadModel:
    def __init__(self, store): self.store=store
    def repository(self, repository:str)->dict:
        with self.store.connection() as c:
            nodes=[dict(r) for r in c.execute(
                "SELECT * FROM mission_graph_nodes WHERE repository=? ORDER BY sequence,node_id",(repository,)).fetchall()]
            prs=[dict(r) for r in c.execute(
                "SELECT * FROM pull_requests WHERE repository=? ORDER BY pr_number",(repository,)).fetchall()]
            bindings=[dict(r) for r in c.execute(
                "SELECT * FROM pr_task_bindings WHERE repository=? ORDER BY pr_number,task_id",(repository,)).fetchall()]
        children=defaultdict(list)
        for n in nodes: children[n["parent_id"]].append(n)
        bound=defaultdict(list)
        by_pr={(p["repository"],p["pr_number"]):p for p in prs}
        for b in bindings:
            p=by_pr.get((b["repository"],b["pr_number"]))
            if p: bound[b["task_id"]].append({**p,"binding":b})
        def tree(node):
            return {**node,"children":[tree(x) for x in children[node["node_id"]]],
                    "prs":bound.get(node["node_id"],[])}
        roots=[tree(n) for n in children[None]]
        ci_green=sum(1 for p in prs if p["ci_state"]=="GREEN")
        merged=sum(1 for p in prs if p["merge_state"]=="MERGED")
        verified=sum(1 for p in prs if p["post_merge_verified"])
        return {"repository":repository,"graph":roots,"pr_summary":{
            "total":len(prs),"ci_green":ci_green,"merged":merged,"post_merge_verified":verified}}
