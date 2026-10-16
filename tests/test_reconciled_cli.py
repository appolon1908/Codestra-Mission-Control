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
        "fabric-health", "fabric-policy-validate", "serve-fabric-api",
        "dispatch-proof", "dispatch-run", "dispatch-state", "dispatch-serve",
        "inventory-scan", "inventory-drift", "serve-inventory-api",
        "node-probe", "record-evidence", "sync-observe", "implementation-proof",
    ]:
        assert command in result.stdout
