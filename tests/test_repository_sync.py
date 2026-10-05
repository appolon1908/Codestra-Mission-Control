from mission_control.store import MissionStore
from mission_control.repository_sync import RepositorySyncStore


def test_repo_sync_state_is_durable_and_updates(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    r = RepositorySyncStore(s)
    r.initialize()
    r.upsert_repo(
        "WhatsApp",
        default_branch="development",
        remote_head_sha="a",
        local_head_sha="a",
        local_branch="development",
        dirty=False,
        open_prs=6,
        ci_state="GREEN",
        sync_state="SYNCED",
    )
    x = r.snapshot("WhatsApp")
    assert x["open_prs"] == 6 and x["sync_state"] == "SYNCED" and x["remote_head_sha"] == "a"
    r.upsert_repo(
        "WhatsApp",
        default_branch="development",
        remote_head_sha="b",
        local_head_sha="a",
        local_branch="development",
        behind=1,
        sync_state="REMOTE_AHEAD",
    )
    assert r.snapshot("WhatsApp")["sync_state"] == "REMOTE_AHEAD"
