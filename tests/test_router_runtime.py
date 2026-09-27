from mission_control.router_runtime import build


def test_router_runtime_build_initializes_store(tmp_path):
    store = build(str(tmp_path / "router.db"))
    assert store.tasks() == []
