"""Offline contract for externally exposed Mission Control staging dashboard.

The actual Keycloak / Caddy / Kong fragments are owned by separate repositories;
their exact-head runtime agreement must be certified after staged deployment.
"""
import json
import re
from pathlib import Path

from mission_control.dashboard_api import DashboardAPI
from mission_control.security import ROLE_PERMISSIONS

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "deploy/staging/gateway-readonly-contract.json"
API = ROOT / "src/mission_control/dashboard_api.py"
ROUTES = {
    "contract", "repositories", "repository", "agents", "tasks",
    "task", "local-work", "sources", "notifications",
}


def test_gateway_exposes_only_scoped_read_paths():
    cfg = json.loads(CONTRACT.read_text())
    assert cfg["schema"] == "codestra.mission-control.gateway-readonly.v1"
    assert cfg["state"] == "STAGING_CANDIDATE_NOT_ACTIVATED"
    assert cfg["methods"] == ["GET"]
    assert set(cfg["read_routes"]) == ROUTES
    assert len(cfg["read_routes"]) == len(ROUTES)
    assert cfg["strip_path"] is False
    source = API.read_text()
    local_routes = set(re.findall(r'if p\.path==PREFIX\+"/([^"]+)"', source))
    assert ROUTES <= local_routes
    assert {"health", "monitoring-lock", "launch-readiness"}.isdisjoint(ROUTES)


def test_staging_gateway_claims_match_backend_role_permission():
    cfg = json.loads(CONTRACT.read_text())
    jwt = cfg["jwt"]
    assert jwt["issuer"] == "https://auth-staging.codestra.co/realms/codestra"
    assert jwt["audience"] == "mission-control-backend"
    assert jwt["algorithm"] == "RS256"
    assert jwt["azp_allowlist"] == ["mission-control-ui"]
    assert jwt["required_gateway_scope"] == "dashboard.read"
    assert jwt["required_permission"] == "dashboard:read"
    for role in ("Operator", "Reviewer"):
        assert "dashboard:read" in ROLE_PERMISSIONS[role]
    for role in ("Viewer", "Agent"):
        assert "dashboard:read" not in ROLE_PERMISSIONS[role]


def test_no_production_effects_and_default_backend_mutations_denied():
    cfg = json.loads(CONTRACT.read_text())
    assert cfg["production_go"] is False
    assert cfg["external_effects_enabled"] is False
    assert cfg["security"]["direct_public_listener"] is False
    assert cfg["security"]["strip_identity_headers"] is True
    assert cfg["security"]["exact_sha_certification_required"] is True
    assert DashboardAPI(store=None).allow_mutations is False
    source = API.read_text()
    assert 'if self.server.allow_mutations else "GET, OPTIONS"' in source
    assert '"DASHBOARD_MUTATIONS_DISABLED"' in source
