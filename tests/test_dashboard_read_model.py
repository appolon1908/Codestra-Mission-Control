from mission_control.dashboard_read_model import DashboardReadModel
from mission_control.mission_graph import MissionGraphStore
from mission_control.store import MissionStore

def test_dashboard_drills_repo_to_task_and_pr(tmp_path):
 s=MissionStore(tmp_path/"db");s.initialize();g=MissionGraphStore(s);g.initialize()
 g.add_node("area","Middleware-","AREA","API")
 g.add_node("sub","Middleware-","SUBAREA","Commands","area")
 g.add_node("task","Middleware-","ATOMIC_TASK","POST command","sub")
 g.upsert_pr("Middleware-",12,"h","b","mission/t","OPEN",ci_state="GREEN",merge_state="MERGED",post_merge_verified=True)
 g.bind_pr_task("Middleware-",12,"task","area","sub",None,"/platform/v1/commands")
 data=DashboardReadModel(s).repository("Middleware-")
 task=data["graph"][0]["children"][0]["children"][0]
 assert task["node_id"]=="task" and task["prs"][0]["pr_number"]==12
 assert data["pr_summary"]=={"total":1,"ci_green":1,"merged":1,"post_merge_verified":1}
