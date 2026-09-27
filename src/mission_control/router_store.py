from __future__ import annotations

import json
from dataclasses import asdict
from typing import Iterable

from .mission_router import AgentCapacity, AtomicTask, MissionRouter


class RouterStore:
    def __init__(self, store) -> None:
        self.store = store

    def initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS development_areas (
                repository TEXT NOT NULL,
                area_id TEXT NOT NULL,
                title TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                target_percent REAL NOT NULL DEFAULT 100,
                PRIMARY KEY (repository, area_id)
            );
            CREATE TABLE IF NOT EXISTS development_subareas (
                repository TEXT NOT NULL,
                area_id TEXT NOT NULL,
                subarea_id TEXT NOT NULL,
                title TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                PRIMARY KEY (repository, area_id, subarea_id)
            );
            CREATE TABLE IF NOT EXISTS atomic_tasks (
                task_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                area_id TEXT NOT NULL,
                subarea_id TEXT NOT NULL,
                mission_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                completion_percent REAL NOT NULL DEFAULT 0,
                certified INTEGER NOT NULL DEFAULT 0,
                dependencies_json TEXT NOT NULL DEFAULT '[]',
                required_skills_json TEXT NOT NULL DEFAULT '[]',
                collision_keys_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS agent_capabilities (
                agent_id TEXT PRIMARY KEY,
                skills_json TEXT NOT NULL DEFAULT '[]',
                active_tasks INTEGER NOT NULL DEFAULT 0,
                wip_limit INTEGER NOT NULL DEFAULT 1
            );
            """)

    def upsert_area(self, repository: str, area_id: str, title: str, sequence: int) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO development_areas(repository,area_id,title,sequence)
                VALUES(?,?,?,?) ON CONFLICT(repository,area_id)
                DO UPDATE SET title=excluded.title, sequence=excluded.sequence""",
                (repository, area_id, title, sequence),
            )

    def upsert_subarea(self, repository: str, area_id: str, subarea_id: str, title: str, sequence: int) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO development_subareas(repository,area_id,subarea_id,title,sequence)
                VALUES(?,?,?,?,?) ON CONFLICT(repository,area_id,subarea_id)
                DO UPDATE SET title=excluded.title, sequence=excluded.sequence""",
                (repository, area_id, subarea_id, title, sequence),
            )

    def upsert_task(self, task: AtomicTask) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO atomic_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                priority=excluded.priority, completion_percent=excluded.completion_percent,
                certified=excluded.certified, dependencies_json=excluded.dependencies_json,
                required_skills_json=excluded.required_skills_json,
                collision_keys_json=excluded.collision_keys_json""",
                (task.task_id, task.repository, task.area, task.sub_area, task.mission_id,
                 task.priority, task.completion_percent, int(task.certified),
                 json.dumps(task.dependencies), json.dumps(sorted(task.required_skills)),
                 json.dumps(sorted(task.collision_keys))),
            )

    def set_agent(self, agent: AgentCapacity) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO agent_capabilities VALUES(?,?,?,?)
                ON CONFLICT(agent_id) DO UPDATE SET skills_json=excluded.skills_json,
                active_tasks=excluded.active_tasks,wip_limit=excluded.wip_limit""",
                (agent.agent_id, json.dumps(sorted(agent.skills)), agent.active_tasks, agent.wip_limit),
            )

    def tasks(self, repository: str | None = None) -> list[AtomicTask]:
        sql = "SELECT * FROM atomic_tasks"
        args: tuple = ()
        if repository:
            sql += " WHERE repository=?"
            args = (repository,)
        with self.store.connection() as conn:
            rows = conn.execute(sql + " ORDER BY priority,task_id", args).fetchall()
        return [AtomicTask(
            task_id=r["task_id"], repository=r["repository"], area=r["area_id"],
            sub_area=r["subarea_id"], mission_id=r["mission_id"], priority=r["priority"],
            completion_percent=r["completion_percent"], certified=bool(r["certified"]),
            dependencies=tuple(json.loads(r["dependencies_json"])),
            required_skills=frozenset(json.loads(r["required_skills_json"])),
            collision_keys=frozenset(json.loads(r["collision_keys_json"])),
        ) for r in rows]

    def agent(self, agent_id: str) -> AgentCapacity | None:
        with self.store.connection() as conn:
            r = conn.execute("SELECT * FROM agent_capabilities WHERE agent_id=?", (agent_id,)).fetchone()
        if not r:
            return None
        return AgentCapacity(r["agent_id"], frozenset(json.loads(r["skills_json"])), r["active_tasks"], r["wip_limit"])

    def snapshot(self, repository: str, *, router: MissionRouter | None = None) -> dict:
        router = router or MissionRouter()
        tasks = self.tasks(repository)
        with self.store.connection() as conn:
            areas = [dict(r) for r in conn.execute(
                "SELECT * FROM development_areas WHERE repository=? ORDER BY sequence", (repository,)
            ).fetchall()]
            subareas = [dict(r) for r in conn.execute(
                "SELECT * FROM development_subareas WHERE repository=? ORDER BY area_id,sequence", (repository,)
            ).fetchall()]
        return {"repository": repository, "progress": router.progress(tasks),
                "areas": areas, "subareas": subareas,
                "tasks": [{**asdict(t), "required_skills": sorted(t.required_skills),
                           "collision_keys": sorted(t.collision_keys)} for t in tasks]}
