from mission_control.mission_router import AgentCapacity, AtomicTask, MissionRouter


def test_router_rejects_wip_saturated_agent():
    router = MissionRouter()
    agent = AgentCapacity("a1", frozenset({"python"}), active_tasks=1, wip_limit=1)
    task = AtomicTask(
        "t1", "Middleware-", "api", "contracts", "m1", required_skills=frozenset({"python"})
    )
    assert router.rank([task], agent) == []


def test_router_filters_dependency_collision_and_skill_risks():
    router = MissionRouter()
    agent = AgentCapacity("a1", frozenset({"python", "api"}))
    tasks = [
        AtomicTask(
            "blocked",
            "Middleware-",
            "api",
            "surface",
            "m1",
            dependencies=("dep",),
            required_skills=frozenset({"python"}),
        ),
        AtomicTask(
            "collision",
            "Middleware-",
            "api",
            "surface",
            "m1",
            collision_keys=frozenset({"src/api.py"}),
            required_skills=frozenset({"python"}),
        ),
        AtomicTask(
            "skill", "Middleware-", "security", "jwt", "m2", required_skills=frozenset({"security"})
        ),
        AtomicTask(
            "ready",
            "Middleware-",
            "api",
            "contracts",
            "m3",
            completion_percent=45,
            required_skills=frozenset({"python", "api"}),
        ),
    ]
    ranked = router.rank(tasks, agent, active_collision_keys={"src/api.py"})
    assert [row.task.task_id for row in ranked] == ["ready"]


def test_router_nudges_lagging_area_and_keeps_certified_progress_separate():
    router = MissionRouter(target_floor=60)
    agent = AgentCapacity("a1", frozenset({"python"}))
    lagging = AtomicTask(
        "lag",
        "Middleware-",
        "workers",
        "execution",
        "m1",
        completion_percent=20,
        required_skills=frozenset({"python"}),
    )
    advanced = AtomicTask(
        "advanced",
        "Middleware-",
        "workers",
        "execution",
        "m2",
        completion_percent=75,
        required_skills=frozenset({"python"}),
    )
    ranked = router.rank([advanced, lagging], agent)
    assert ranked[0].task.task_id == "lag"
    progress = router.progress(
        [
            lagging,
            AtomicTask(
                "done",
                "Middleware-",
                "api",
                "surface",
                "m3",
                completion_percent=100,
                certified=True,
            ),
        ]
    )
    assert progress["work_in_progress"] == 60.0
    assert progress["certified"] == 50.0
