import json
import os
import subprocess
import sys
from pathlib import Path

from mission_control.store import MissionStore


def test_repositories_cli(tmp_path):
    db = tmp_path / "mission.db"
    store = MissionStore(db)
    store.initialize()
    store.upsert_repository(
        "RepoA",
        full_name="owner/RepoA",
        local_path="/repos/RepoA",
        origin_url="https://example.invalid/RepoA.git",
        default_branch="main",
        visibility="private",
        local_present=True,
        mission_channel_path="/repos/RepoA/.codestra-mission",
        workspace_path="/hub/RepoA.code-workspace",
    )
    env = dict(os.environ)
    src = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(src)
    out = subprocess.check_output(
        [sys.executable, "-m", "mission_control.cli", "--db", str(db), "repositories"],
        env=env,
        text=True,
    )
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["local"] == 1
    assert payload["missing"] == 0
