import pytest

from mission_control.mission_graph import MissionGraphStore
from mission_control.mission_publisher import MissionPublisher
from mission_control.mission_router import AtomicTask
from mission_control.router_store import RouterStore
from mission_control.store import MissionStore


def setup(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    RouterStore(s).initialize()
    MissionGraphStore(s).initialize()
    return s


def test_publish_is_atomic_across_graph_and_router(tmp_path):
    s = setup(tmp_path)
    p = MissionPublisher(s)
    t = AtomicTask("T1", "WhatsApp", "API", "AI-Drafts", "M1", required_skills=frozenset({"api"}))
    r = p.publish_task(t, area_title="API", subarea_title="AI Drafts", task_title="Finish section")
    assert r.graph_registered and r.router_registered
    p.assert_consistent("T1")
    with s.connection() as c:
        assert (
            c.execute("select count(*) n from atomic_tasks where task_id='T1'").fetchone()["n"] == 1
        )
        assert (
            c.execute("select count(*) n from mission_graph_nodes where node_id='T1'").fetchone()[
                "n"
            ]
            == 1
        )


def test_detects_old_one_sided_registration(tmp_path):
    s = setup(tmp_path)
    with s.connection() as c:
        c.execute(
            "insert into mission_graph_nodes values('T2','WhatsApp','ATOMIC_TASK',NULL,'bad',1,'{}')"
        )
    with pytest.raises(ValueError, match="drift"):
        MissionPublisher(s).assert_consistent("T2")
