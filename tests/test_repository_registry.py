from mission_control.store import MissionStore


def test_repository_registry_round_trip(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_repository(
        "Middleware-",
        full_name="ingtrader21-spec/Middleware-",
        local_path="/repos/Middleware-",
        origin_url="https://example.invalid/Middleware-.git",
        default_branch="main",
        visibility="private",
        local_present=True,
        mission_channel_path="/repos/Middleware-/.codestra-mission",
        workspace_path="/hub/Middleware-.code-workspace",
    )
    rows = store.list_repositories()
    assert len(rows) == 1
    assert rows[0]["repository"] == "Middleware-"
    assert rows[0]["local_present"] == 1
