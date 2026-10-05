from __future__ import annotations

import json
from dataclasses import dataclass

from .mission_router import AtomicTask


@dataclass(frozen=True)
class PublishResult:
    task_id: str
    graph_registered: bool
    router_registered: bool


class MissionPublisher:
    def __init__(self, store):
        self.store = store

    def publish_task(
        self, task: AtomicTask, *, area_title: str, subarea_title: str, task_title: str
    ) -> PublishResult:
        with self.store.connection() as c:
            c.execute(
                """INSERT INTO development_areas(repository,area_id,title,sequence) VALUES(?,?,?,1)
              ON CONFLICT(repository,area_id) DO UPDATE SET title=excluded.title""",
                (task.repository, task.area, area_title),
            )
            c.execute(
                """INSERT INTO development_subareas(repository,area_id,subarea_id,title,sequence) VALUES(?,?,?,?,1)
              ON CONFLICT(repository,area_id,subarea_id) DO UPDATE SET title=excluded.title""",
                (task.repository, task.area, task.sub_area, subarea_title),
            )
            c.execute(
                """INSERT INTO atomic_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(task_id) DO UPDATE SET repository=excluded.repository,area_id=excluded.area_id,
              subarea_id=excluded.subarea_id,mission_id=excluded.mission_id,priority=excluded.priority,
              dependencies_json=excluded.dependencies_json,required_skills_json=excluded.required_skills_json,
              collision_keys_json=excluded.collision_keys_json""",
                (
                    task.task_id,
                    task.repository,
                    task.area,
                    task.sub_area,
                    task.mission_id,
                    task.priority,
                    task.completion_percent,
                    int(task.certified),
                    json.dumps(task.dependencies),
                    json.dumps(sorted(task.required_skills)),
                    json.dumps(sorted(task.collision_keys)),
                ),
            )
            area_node = f"{task.repository}:{task.area}"
            sub_node = f"{task.repository}:{task.area}:{task.sub_area}"
            c.execute(
                """INSERT INTO mission_graph_nodes(node_id,repository,node_type,parent_id,title,sequence,metadata_json)
              VALUES(?,?,?,?,?,1,'{}') ON CONFLICT(node_id) DO UPDATE SET title=excluded.title""",
                (area_node, task.repository, "AREA", None, area_title),
            )
            c.execute(
                """INSERT INTO mission_graph_nodes(node_id,repository,node_type,parent_id,title,sequence,metadata_json)
              VALUES(?,?,?,?,?,1,'{}') ON CONFLICT(node_id) DO UPDATE SET parent_id=excluded.parent_id,title=excluded.title""",
                (sub_node, task.repository, "SUBAREA", area_node, subarea_title),
            )
            c.execute(
                """INSERT INTO mission_graph_nodes(node_id,repository,node_type,parent_id,title,sequence,metadata_json)
              VALUES(?,?,?,?,?,1,'{}') ON CONFLICT(node_id) DO UPDATE SET parent_id=excluded.parent_id,title=excluded.title""",
                (task.task_id, task.repository, "ATOMIC_TASK", sub_node, task_title),
            )
        return PublishResult(task.task_id, True, True)

    def assert_consistent(self, task_id: str) -> None:
        with self.store.connection() as c:
            graph = c.execute(
                "SELECT repository FROM mission_graph_nodes WHERE node_id=? AND node_type='ATOMIC_TASK'",
                (task_id,),
            ).fetchone()
            router = c.execute(
                "SELECT repository FROM atomic_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if not graph or not router or graph["repository"] != router["repository"]:
            raise ValueError("mission_graph_router_drift")
