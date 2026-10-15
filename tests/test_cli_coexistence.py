"""Read-only feature commands share a CLI without opening the mission ledger."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mission_control import cli


def test_repository_and_fabric_checks_coexist_without_initializing_ledger(
    tmp_path, monkeypatch, capsys
):
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "-b", "feature/coexistence"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "base",
        ],
        check=True, capture_output=True,
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "tailnet": "test.ts.net",
        "nodes": [{
            "hostname": "control", "dns": "control.test.ts.net.",
            "ipv4": "100.64.0.1", "roles": ["mission-control"],
        }],
    }), encoding="utf-8")
    snapshot = tmp_path / "status.json"
    snapshot.write_text(json.dumps({
        "BackendState": "Running",
        "Self": {
            "HostName": "control", "DNSName": "control.test.ts.net.",
            "TailscaleIPs": ["100.64.0.1"], "Online": True,
            "Tags": ["tag:mission-control"],
        },
        "Peer": {},
    }), encoding="utf-8")
    database = tmp_path / "absent" / "ledger.db"

    def reject_store(path):
        raise AssertionError("read-only feature command initialized the mission ledger")

    monkeypatch.setattr(cli, "_store", reject_store)

    def run(*arguments, exit_code=None):
        monkeypatch.setattr(sys, "argv", ["mission-control", "--db", str(database), *arguments])
        if exit_code is None:
            cli.main()
        else:
            with pytest.raises(SystemExit) as error:
                cli.main()
            assert error.value.code == exit_code
        assert not database.parent.exists()
        return json.loads(capsys.readouterr().out)

    repository = run("repo-sync-status", "--repo", str(repo))
    assert repository["status"]["branch"] == "feature/coexistence"
    publication = run("publish-readiness", "--repo", str(repo), exit_code=2)
    assert publication["reasons"] == ["remote_not_configured:codestra-local"]
    fabric = run(
        "fabric-health", "--inventory", str(inventory), "--status-json", str(snapshot), exit_code=0,
    )
    assert fabric["state"] == "healthy"
    assert fabric["counts"] == {"online": 1, "unexpected": 0}
    policy_path = Path(__file__).resolve().parents[1] / "config" / "tailscale.policy-plan.json"
    policy = run("fabric-policy-validate", "--policy", str(policy_path), exit_code=0)
    assert policy["valid"] is True
