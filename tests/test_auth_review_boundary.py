import pytest

from mission_control.security import KeycloakVerifier


def test_openbao_branch_requires_authentication_by_default(monkeypatch):
    monkeypatch.delenv("MISSION_CONTROL_AUTH_MODE", raising=False)
    monkeypatch.delenv("MISSION_CONTROL_JWT_ISSUER", raising=False)
    with pytest.raises(RuntimeError, match="JWT_ISSUER"):
        KeycloakVerifier()


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_openbao_branch_rejects_disabled_auth_in_protected_environment(monkeypatch, environment):
    monkeypatch.setenv("MISSION_CONTROL_ENV", environment)
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "disabled")
    with pytest.raises(RuntimeError, match="cannot be disabled"):
        KeycloakVerifier()
