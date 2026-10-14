import json
import subprocess
import sys

import pytest

from mission_control import cli
from mission_control.repo_sync import AuthState, GitHubAuth, RepoSyncInspector


@pytest.mark.parametrize("command", ["repo-sync-status", "publish-readiness", "auth-readiness"])
def test_readonly_inspector_commands_do_not_create_a_ledger(tmp_path, monkeypatch, capsys, command):
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "feature/read-only"], check=True, capture_output=True)
    subprocess.run([
        "git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
        "commit", "--allow-empty", "-m", "base",
    ], check=True, capture_output=True)

    class OfflineInspector(RepoSyncInspector):
        def github_auth(self):
            return GitHubAuth(AuthState.UNAVAILABLE, "offline test")

    monkeypatch.setattr(cli, "RepoSyncInspector", OfflineInspector)
    database = tmp_path / "must-not-exist" / "ledger.db"
    args = ["mission-control", "--db", str(database), command]
    if command != "auth-readiness":
        args.extend(["--repo", str(repo)])
    monkeypatch.setattr(sys, "argv", args)
    if command == "publish-readiness":
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
    else:
        cli.main()
    assert isinstance(json.loads(capsys.readouterr().out), dict)
    assert not database.parent.exists(), "read-only inspection must not initialize a ledger"
