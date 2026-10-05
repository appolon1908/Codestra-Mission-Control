from mission_control.mission_graph import MissionGraphStore
from mission_control.store import MissionStore


def setup(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    g = MissionGraphStore(s)
    g.initialize()
    return g


def test_graph_tracks_area_subarea_feature_api_and_task(tmp_path):
    g = setup(tmp_path)
    g.add_node("area-api", "Middleware-", "AREA", "API")
    g.add_node("sub-command", "Middleware-", "SUBAREA", "Command API", "area-api")
    g.add_node("feature-create", "Middleware-", "FEATURE", "Create command", "sub-command")
    g.add_node(
        "task-post",
        "Middleware-",
        "ATOMIC_TASK",
        "POST command",
        "feature-create",
        metadata={"api": "/platform/v1/commands"},
    )
    g.add_edge("task-post", "identity-task")
    with g.store.connection() as c:
        assert (
            c.execute(
                "SELECT parent_id FROM mission_graph_nodes WHERE node_id='task-post'"
            ).fetchone()["parent_id"]
            == "feature-create"
        )


def test_pr_is_first_class_and_bound_to_exact_task(tmp_path):
    g = setup(tmp_path)
    g.upsert_pr(
        "Middleware-",
        280,
        "head1",
        "base1",
        "mission/task",
        "OPEN",
        implementation_agent="codex-4",
        review_agent="claude-2",
        test_agent="api-1",
        ci_state="GREEN",
    )
    g.bind_pr_task(
        "Middleware-", 280, "task-post", "api", "command", "create", "/platform/v1/commands"
    )
    rows = g.task_prs("task-post")
    assert rows[0]["pr_number"] == 280 and rows[0]["ci_state"] == "GREEN"
    assert rows[0]["api_contract"] == "/platform/v1/commands"
