"""Feature commands and governed main commands must remain available together."""
import subprocess
import sys


def test_reconciled_cli_exposes_feature_and_main_commands():
    result = subprocess.run(
        [sys.executable, "-m", "mission_control.cli", "--help"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    for command in [
        "repo-sync-status", "publish-readiness", "auth-readiness", "serve-repo-sync-api",
        "fabric-health", "fabric-policy-validate", "serve-fabric-api",
        "dispatch-proof", "dispatch-run", "dispatch-state", "dispatch-serve",
        "inventory-scan", "inventory-drift", "serve-inventory-api",
        "node-probe", "record-evidence", "sync-observe", "implementation-proof",
    ]:
        assert command in result.stdout


def test_repo_inspectors_do_not_initialize_database(tmp_path, monkeypatch, capsys):
    from mission_control import cli

    def reject_store(path):
        raise AssertionError("read-only inspector initialized the mission store")

    monkeypatch.setattr(cli, "_store", reject_store)
    database = tmp_path / "missing" / "mission.db"
    for command in ["repo-sync-status", "publish-readiness", "auth-readiness"]:
        arguments = ["mission-control", "--db", str(database), command]
        if command != "auth-readiness":
            arguments.extend(["--repo", str(tmp_path)])
        monkeypatch.setattr(sys, "argv", arguments)
        try:
            cli.main()
        except SystemExit as exc:
            assert exc.code in {2, 3}
        assert not database.parent.exists()
        assert capsys.readouterr().out
