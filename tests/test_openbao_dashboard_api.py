"""OpenBao dashboard contract: real API data, no release/secret mutations."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from mission_control import dashboard_api
from mission_control.agent_registry import AgentRegistry
from mission_control.mission_graph import MissionGraphStore
from mission_control.mission_router import AtomicTask
from mission_control.openbao_read_model import snapshot
from mission_control.oversight import OversightStore
from mission_control.store import MissionStore


def test_read_model_distinguishes_missing_data_from_certification():
    empty = snapshot([], {"total": 0}, [])
    assert empty["data_state"] == "NO_REGISTRY_DATA"
    assert empty["runtime_health"] == "NOT_CHECKED"
    assert empty["release_certification"] == "NOT_CHECKED"
    assert empty["sections"] == []
    assert empty["registry"]["open_prs"] is None
    t = AtomicTask("OB-15-04", "Codestra-OpenBao", "CI/CD", "Promotion", "OB", completion_percent=58)
    result = snapshot([
        {"repository": "Codestra-OpenBao", "sync_state": "REMOTE_AHEAD",
         "ci_state": "RED", "open_prs": 2}
    ], {"total": 3, "ci_green": 1}, [t])
    assert result["registry"]["open_prs"] == 2
    assert result["sections"][0]["tasks"][0]["completion_percent"] == 58
    assert result["release_certification"] == "NOT_CHECKED"
    assert "token" not in json.dumps(result).lower()


def test_endpoint_requires_mission_read_and_returns_json(tmp_path, monkeypatch):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    MissionGraphStore(s).initialize()
    AgentRegistry(s).initialize()
    OversightStore(s).initialize()

    class Verifier:
        mode = "required"
        def require(self, authorization, permission):
            assert permission == "mission:read"
            if authorization != "Bearer local-test":
                raise dashboard_api.AuthError("missing_bearer_token", 401)
            return object()
    monkeypatch.setattr(dashboard_api, "KeycloakVerifier", Verifier)
    server = dashboard_api.DashboardAPI(s).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        url = f"http://{host}:{port}/platform/v1/dashboard/openbao"
        try:
            urlopen(url)
            assert False, "must reject missing credentials"
        except HTTPError as exc:
            assert exc.code == 401
        from urllib.request import Request
        req = Request(url, headers={"Authorization": "Bearer local-test"})
        with urlopen(req) as response:
            body = json.load(response)
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
        assert body["repository"] == "Codestra-OpenBao"
        assert body["data_state"] == "NO_REGISTRY_DATA"
        assert body["mode"] == "READ_ONLY"
        # Health is an explicit second read-only endpoint; server configuration
        # is absent in this unit test, so no external network can be contacted.
        monkeypatch.delenv("OPENBAO_HEALTH_URL", raising=False)
        monkeypatch.delenv("OPENBAO_HEALTH_ALLOWED_HOSTS", raising=False)
        with urlopen(Request(f"http://{host}:{port}/platform/v1/dashboard/openbao/health",
                            headers={"Authorization": "Bearer local-test"})) as response:
            health = json.load(response)
        assert health == {"state": "NOT_CONFIGURED"}
        try:
            urlopen(f"http://{host}:{port}/platform/v1/dashboard/openbao/health")
            assert False, "health endpoint requires mission:read"
        except HTTPError as exc:
            assert exc.code == 401
        with urlopen(Request(f"http://{host}:{port}/platform/v1/dashboard/contract",
                            headers={"Authorization": "Bearer local-test"})) as response:
            contract = json.load(response)
        assert contract["endpoints"]["openbao"]["path"] == "/platform/v1/dashboard/openbao"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
