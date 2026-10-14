import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def contract():
    return json.loads((ROOT / ".codestra/deployment-contract.json").read_text())


def test_deployment_role_conforms_to_governing_taxonomy():
    policy = (ROOT / ".codestra/MIDDLEWARE-INTEGRATION-CONTRACT.md").read_text()
    roles = policy.split("Each repo must be classified as one of: ", 1)[1].split(".", 1)[0]
    assert contract()["role"] in roles.split("; ")


def test_deployment_declares_accountable_owner():
    assert contract().get("owner") == "appolon1908"


def test_mission_control_has_no_public_ingress_or_enabled_effects():
    deployment = contract()
    integration = deployment["canonical_integration"]
    assert integration.get("ingress") == "tailnet-only"
    assert integration["public_edge"] is None
    assert integration["internal_and_metrics_public"] is False
    assert deployment["production_effects"]["default_enabled"] is False
    assert deployment["promotion"] == ["development", "testing", "staging", "production"]
