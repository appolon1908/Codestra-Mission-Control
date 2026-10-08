"""Run the checked-in Postman contract against a real read-only database with a synthetic RSA test issuer."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mission_control.control_plane_read import ControlPlaneReadModel
from mission_control.dashboard_api import DashboardAPI
from mission_control.runtime import build
from mission_control.security import KeycloakVerifier


def test_newman_authenticated_postgresql_read_only_contract(tmp_path):
    dsn = os.getenv("CODESTRA_CONTROL_TEST_DSN")
    if not dsn:
        pytest.skip("Explicit PostgreSQL integration DSN not provided")
    newman = shutil.which("newman")
    if not newman:
        pytest.skip("Newman CLI not installed")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://synthetic-oidc.invalid/realms/test"
    verifier = KeycloakVerifier(
        issuer=issuer,
        mode="required",
        audience="mission-control-backend",
        azp={"mission-control-ui"},
    )
    verifier._jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())
    )
    server = DashboardAPI(
        build(tmp_path / "postman.sqlite"),
        authorization=verifier,
        control_plane=ControlPlaneReadModel(dsn),
    ).server("127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "ci-integration-only",
            "iss": issuer,
            "aud": "mission-control-backend",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=2)).timestamp()),
            "azp": "mission-control-ui",
            "realm_access": {"roles": ["Operator"]},
        },
        private,
        algorithm="RS256",
        headers={"kid": "synthetic-ci"},
    )
    try:
        collection = (
            Path(__file__).resolve().parents[1]
            / "postman/Codestra-Mission-Control-ReadOnly.postman_collection.json"
        )
        run = subprocess.run(
            [
                newman,
                "run",
                str(collection),
                "--env-var",
                f"baseUrl=http://127.0.0.1:{server.server_port}",
                "--env-var",
                f"accessToken={token}",
                "--env-var",
                "repository=Middleware-",
                "--timeout-request",
                "8000",
                "--bail",
            ],
            capture_output=True,
            text=True,
            timeout=75,
            check=False,
        )
        # Never print a token or raw auth header in failure reports.
        assert run.returncode == 0, (
            "Newman read-only dashboard checks failed (see local CI logs without token output)."
        )
        assert "failed" in run.stdout
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
