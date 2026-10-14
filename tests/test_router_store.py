import json
import threading
from urllib.request import urlopen

from mission_control.mission_router import AgentCapacity, AtomicTask
from mission_control.router_api import MissionRouterAPI
from mission_control.router_store import RouterStore
from mission_control.store import MissionStore


def make_store(tmp_path):
    base = MissionStore(tmp_path / "mc.db")
    base.initialize()
    store = RouterStore(base)
    store.initialize()
    return store


def test_hierarchy_snapshot_and_progress(tmp_path):
    store = make_store(tmp_path)
    store.upsert_area("Middleware-", "api", "API Surface", 1)
    store.upsert_subarea("Middleware-", "api", "contracts", "Contracts", 1)
    store.upsert_task(
        AtomicTask("T1", "Middleware-", "api", "contracts", "M1", completion_percent=70)
    )
    snap = store.snapshot("Middleware-")
    assert snap["areas"][0]["title"] == "API Surface"
    assert snap["tasks"][0]["sub_area"] == "contracts"
    assert snap["progress"] == {"work_in_progress": 70.0, "certified": 0.0}


def test_next_task_api_uses_agent_skills(tmp_path):
    store = make_store(tmp_path)
    store.set_agent(AgentCapacity("agent-1", frozenset({"python", "api"})))
    store.upsert_task(
        AtomicTask(
            "T1",
            "Middleware-",
            "api",
            "contracts",
            "M1",
            required_skills=frozenset({"python", "api"}),
        )
    )
    store.upsert_task(
        AtomicTask(
            "T2", "Middleware-", "security", "jwt", "M2", required_skills=frozenset({"security"})
        )
    )
    server = MissionRouterAPI(store).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(
            f"http://{host}:{port}/platform/v1/mission-router/next?repository=Middleware-&agent_id=agent-1"
        ) as response:
            payload = json.load(response)
        assert [row["task_id"] for row in payload["next"]] == ["T1"]
    finally:
        server.shutdown()
        server.server_close()
