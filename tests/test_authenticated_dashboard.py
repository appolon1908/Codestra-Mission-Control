"""No unauthenticated control-plane disclosure; PostgreSQL is queried read-only."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mission_control.control_plane_read import ControlPlaneReadModel
from mission_control.dashboard_api import DashboardAPI
from mission_control.runtime import build
from mission_control.security import KeycloakVerifier


@pytest.fixture
def dashboard(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://identity.example.invalid/realms/codestra"
    verifier = KeycloakVerifier(
        issuer=issuer,
        audience="mission-control-backend",
        azp={"mission-control-ui"},
        mode="required",
    )
    verifier._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )
    # Use a deliberately unconfigured database for fail-closed API tests.
    server = DashboardAPI(
        build(tmp_path / "missions.sqlite"),
        authorization=verifier,
        control_plane=ControlPlaneReadModel(dsn=""),
    ).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(
        path,
        *,
        roles=("Operator",),
        bearer=True,
        issuer_override=None,
        audience=None,
        azp="mission-control-ui",
        origin=None,
    ):
        headers = {}
        if bearer:
            now = datetime.now(UTC)
            claims = {
                "sub": "test-operator",
                "iss": issuer_override or issuer,
                "aud": audience or "mission-control-backend",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=2)).timestamp()),
                "azp": azp,
                "realm_access": {"roles": list(roles)},
            }
            headers["Authorization"] = "Bearer " + jwt.encode(
                claims, key, algorithm="RS256", headers={"kid": "local-test"}
            )
        if origin:
            headers["Origin"] = origin
        req = Request(f"http://127.0.0.1:{server.server_port}" + path, headers=headers)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response), dict(response.headers)
        except HTTPError as err:
            return err.code, json.load(err), dict(err.headers)

    yield request
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_denies_anonymous_and_wrong_role(dashboard):
    path = "/platform/v1/dashboard/repositories"
    assert dashboard(path, bearer=False)[0] == 401
    assert dashboard(path, roles=("Viewer",))[0] == 403
    assert dashboard(path, roles=("Agent",))[0] == 403


def test_rejects_invalid_token_context(dashboard):
    path = "/platform/v1/dashboard/contract"
    assert dashboard(path, issuer_override="https://other.example.invalid/realms/x")[0] == 401
    assert dashboard(path, audience="other-service")[0] == 401
    assert dashboard(path, azp="evil-client")[0] == 403


def test_backend_fail_closed_when_data_source_missing(dashboard):
    assert dashboard("/healthz", bearer=False)[0] == 200
    assert dashboard("/readyz", bearer=False)[0] == 503
    status, data, headers = dashboard("/platform/v1/dashboard/repositories")
    assert status == 503 and data["error_code"] == "SOURCE_UNAVAILABLE"
    assert headers.get("Cache-Control") == "no-store"
    assert headers.get("X-Content-Type-Options") == "nosniff"
    status, contract, _ = dashboard("/platform/v1/dashboard/contract")
    assert status == 200 and "tasks" in contract["endpoints"]
    status, _, _ = dashboard("/platform/v1/dashboard/tasks?repository=Middleware-")
    assert status == 503


def test_rejects_arbitrary_origin(dashboard):
    assert dashboard("/platform/v1/dashboard/contract", origin="https://evil.example")[0] == 403


def test_live_postgres_projection_is_real_and_read_only():
    dsn = os.getenv("CODESTRA_CONTROL_TEST_DSN")
    if not dsn:
        pytest.skip(
            "Set CODESTRA_CONTROL_TEST_DSN explicitly for read-only live PostgreSQL certification"
        )
    model = ControlPlaneReadModel(dsn=dsn)
    repos = model.repositories()
    assert len(repos) >= 5
    middleware = next(r for r in repos if r["repository"] == "Middleware-")
    assert middleware["total_workstations"] > 0
    assert middleware["open_prs"] is None and middleware["ci_state"] == "UNVERIFIED"
    assert middleware["external_effects_enabled"] is False
    assert isinstance(model.agents(), list)
    assert isinstance(model.tasks("Middleware-"), list)
    local = model.local_work("Middleware-", 48)
    assert local["source"] == "postgres_workstation_registry" and len(local["lanes"]) > 0
    assert all(x["classification"] == "UNKNOWN_LOCAL_STATE" for x in local["lanes"])
    assert model.task("nonexistent") is None


def test_authenticated_http_returns_real_postgres_rows(tmp_path):
    dsn = os.getenv("CODESTRA_CONTROL_TEST_DSN")
    if not dsn:
        pytest.skip("Requires explicitly supplied read-only control-plane DSN")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://identity.example.invalid/realms/codestra"
    verifier = KeycloakVerifier(
        issuer=issuer,
        audience="mission-control-backend",
        azp={"mission-control-ui"},
        mode="required",
    )
    verifier._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )
    server = DashboardAPI(
        build(tmp_path / "live.sqlite"),
        authorization=verifier,
        control_plane=ControlPlaneReadModel(dsn),
    ).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "test-operator",
            "iss": issuer,
            "aud": "mission-control-backend",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=2)).timestamp()),
            "azp": "mission-control-ui",
            "realm_access": {"roles": ["Operator"]},
        },
        key,
        algorithm="RS256",
        headers={"kid": "local-test"},
    )
    try:
        root = f"http://127.0.0.1:{server.server_port}"
        with urlopen(root + "/readyz", timeout=5) as response:
            assert response.status == 200
        for path, key_name in (
            ("/platform/v1/dashboard/repositories", "repositories"),
            ("/platform/v1/dashboard/agents", "agents"),
            ("/platform/v1/dashboard/tasks?repository=Middleware-", "tasks"),
            ("/platform/v1/dashboard/local-work?repository=Middleware-", "lanes"),
        ):
            with urlopen(
                Request(root + path, headers={"Authorization": "Bearer " + token}), timeout=5
            ) as response:
                data = json.load(response)
            assert isinstance(data[key_name], list)
            if key_name == "repositories":
                assert len(data[key_name]) >= 5
                assert any(x["repository"] == "Middleware-" for x in data[key_name])
            if key_name == "lanes":
                assert len(data[key_name]) >= 1
        with pytest.raises(HTTPError) as exc:
            urlopen(root + "/platform/v1/dashboard/repositories", timeout=5)
        assert exc.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
