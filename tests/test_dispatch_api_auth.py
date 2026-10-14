import json
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mission_control import dispatch_api
from mission_control.checkpoint_dispatch import CheckpointDispatcher
from mission_control.dispatch_api import DispatchAPI
from mission_control.models import Mission
from mission_control.store import MissionStore


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.73", "example.invalid"])
def test_dispatcher_refuses_non_loopback_bind(tmp_path, host, monkeypatch):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    def reject_bind(*args, **kwargs):
        raise AssertionError("server binding was attempted")
    monkeypatch.setattr(dispatch_api, "ThreadingHTTPServer", reject_bind)
    with pytest.raises(ValueError, match="loopback"):
        DispatchAPI(CheckpointDispatcher(store)).server(host)


def test_dispatcher_refuses_unscoped_disabled_auth(tmp_path, monkeypatch):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    monkeypatch.setenv("MISSION_CONTROL_ENV", "")
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "disabled")
    with pytest.raises(RuntimeError, match="development"):
        DispatchAPI(CheckpointDispatcher(store)).server()


@pytest.fixture
def authenticated_api(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://keycloak.example/realms/codestra"
    monkeypatch.setenv("MISSION_CONTROL_ENV", "production")
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "required")
    monkeypatch.setenv("MISSION_CONTROL_JWT_ISSUER", issuer)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *args, **kwargs: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key()),
    ))
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("M", "Codestra-Mission-Control", "test dispatcher auth"))
    dispatcher = CheckpointDispatcher(store)
    server = DispatchAPI(dispatcher).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(method, path, body=None, role=None, subject="agent-1", token_override=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(f"http://127.0.0.1:{server.server_address[1]}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if role:
            now = int(time.time())
            token = jwt.encode({
                "iss": issuer, "sub": subject, "aud": "mission-control-backend",
                "azp": "mission-control-ui", "iat": now, "exp": now + 60,
                "realm_access": {"roles": [role]},
            }, key, algorithm="RS256")
            request.add_header("Authorization", "Bearer " + token)
        if token_override is not None:
            request.add_header("Authorization", "Bearer " + token_override)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    try:
        yield dispatcher, call
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dispatcher_rejects_unauthenticated_reads_and_writes(authenticated_api):
    dispatcher, call = authenticated_api
    assert call("GET", "/health")[0] == 200
    assert call("GET", "/platform/v1/dispatcher/state")[0] == 401
    assert call("POST", "/platform/v1/dispatcher/proofs", {"mission_id": "M", "agent_id": "agent-1"})[0] == 401
    assert call("POST", "/platform/v1/dispatcher/run", {})[0] == 401
    assert dispatcher.proofs("M") == []


def test_dispatcher_enforces_central_permissions(authenticated_api):
    dispatcher, call = authenticated_api
    assert call("GET", "/platform/v1/dispatcher/state", role="Viewer")[0] == 200
    assert call("POST", "/platform/v1/dispatcher/run", {}, role="Viewer")[0] == 403
    assert call("POST", "/platform/v1/dispatcher/proofs", {"mission_id": "M", "agent_id": "agent-1"}, role="Viewer")[0] == 403
    assert call("POST", "/platform/v1/dispatcher/run", {}, role="Operator")[0] == 200
    assert dispatcher.proofs("M") == []


def test_dispatcher_rejects_invalid_signed_identity_before_mutation(authenticated_api):
    dispatcher, call = authenticated_api
    assert call("POST", "/platform/v1/dispatcher/proofs", {"mission_id": "M", "agent_id": "agent-1"}, token_override="invalid")[0] == 401
    assert dispatcher.proofs("M") == []


def test_dispatcher_binds_proof_writer_to_authenticated_identity(authenticated_api):
    dispatcher, call = authenticated_api
    assert call("POST", "/platform/v1/dispatcher/proofs", {"mission_id": "M", "agent_id": "other-agent"}, role="Agent")[0] == 403
    assert dispatcher.proofs("M") == []
    assert call("POST", "/platform/v1/dispatcher/proofs", {"mission_id": "M", "agent_id": "agent-1"}, role="Agent")[0] == 201
    assert len(dispatcher.proofs("M")) == 1
