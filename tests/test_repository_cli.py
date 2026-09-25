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


def test_checkpoint_reconcile_cli_writes_next_task(tmp_path):
    import json
    import os
    import subprocess
    import sys

    db = tmp_path / "mission.db"
    store = MissionStore(db)
    store.initialize()
    channel = tmp_path / "channel"
    store.upsert_repository(
        "repo",
        full_name="org/repo",
        local_path=str(tmp_path / "repo"),
        origin_url=None,
        default_branch="main",
        visibility="private",
        local_present=True,
        mission_channel_path=str(channel),
        workspace_path=None,
    )
    from mission_control.models import Mission
    store.upsert_mission(Mission("PAS-CLI", "repo", "goal"))

    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mission_control.cli",
            "--db",
            str(db),
            "checkpoint",
            "--mission",
            "PAS-CLI",
            "--agent",
            "codex-01",
            "--state",
            "IMPLEMENTED",
            "--head-sha",
            "b" * 40,
            "--dirty-count",
            "0",
            "--tests-json",
            '{"pytest":{"passed":true}}',
            "--request-next-task",
            "--reconcile",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["decision"]["action"] == "REVIEW"
    assert store.get_mission("PAS-CLI")["status"] == "IN_REVIEW"
    assert (channel / "NEXT_TASK.md").is_file()
