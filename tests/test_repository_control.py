from mission_control.store import MissionStore
from mission_control.repository_sync import RepositorySyncStore
from mission_control.repository_control import RepositoryControlCenter
from mission_control.agent_registry import AgentRegistry
from mission_control.router_store import RouterStore


def test_control_center_rolls_repository_operational_state(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    AgentRegistry(s).initialize()
    RouterStore(s).initialize()
    RepositorySyncStore(s).initialize()
    with s.connection() as c:
        c.execute(
            "CREATE TABLE repository_registry(repository TEXT PRIMARY KEY,full_name TEXT,source TEXT,status TEXT,mission_state TEXT)"
        )
        c.execute(
            "INSERT INTO repository_registry VALUES('WhatsApp','ingtrader21-spec/WhatsApp','GitHub','DISCOVERED','PLANNED')"
        )
    RepositorySyncStore(s).upsert_repo(
        "WhatsApp", open_prs=6, ci_state="GREEN", sync_state="SYNCED"
    )
    row = RepositoryControlCenter(s).rows()[0]
    assert row["repository"] == "WhatsApp"
    assert row["open_prs"] == 6 and row["sync_state"] == "SYNCED" and row["ci_state"] == "GREEN"
