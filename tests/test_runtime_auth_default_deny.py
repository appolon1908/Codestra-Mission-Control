from __future__ import annotations

import pytest

from mission_control.runtime import configure_runtime_auth
from mission_control.security import AuthError, KeycloakVerifier


def test_runtime_defaults_to_required_auth_and_issuer(monkeypatch):
    monkeypatch.delenv("MISSION_CONTROL_AUTH_MODE", raising=False)
    monkeypatch.delenv("MISSION_CONTROL_JWT_ISSUER", raising=False)
    with pytest.raises(RuntimeError, match="JWT_ISSUER"):
        configure_runtime_auth("127.0.0.1")
    monkeypatch.setenv("MISSION_CONTROL_JWT_ISSUER", "https://keycloak.example/realms/codestra")
    assert configure_runtime_auth("127.0.0.1") == "required"
    assert KeycloakVerifier().mode == "required"
    with pytest.raises(AuthError, match="missing_bearer_token"):
        KeycloakVerifier().require(None, "mission:read")


def test_disabled_mode_denied_without_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "disabled")
    monkeypatch.setenv("MISSION_CONTROL_JWT_ISSUER", "https://keycloak.example/realms/codestra")
    with pytest.raises(RuntimeError, match="requires Keycloak"):
        configure_runtime_auth("127.0.0.1")


@pytest.mark.parametrize("environment", ["production", "staging", ""])
def test_no_auth_requires_explicit_development_environment(monkeypatch, environment):
    monkeypatch.setenv("MISSION_CONTROL_ENV", environment)
    monkeypatch.delenv("MISSION_CONTROL_AUTH_MODE", raising=False)
    with pytest.raises(RuntimeError, match="loopback development"):
        configure_runtime_auth("127.0.0.1", local_dev_no_auth=True)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.73"])
def test_no_auth_never_binds_public_network(monkeypatch, host):
    monkeypatch.setenv("MISSION_CONTROL_ENV", "development")
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "disabled")
    with pytest.raises(RuntimeError, match="loopback development"):
        configure_runtime_auth(host, local_dev_no_auth=True)


def test_explicit_loopback_development_auth_mode(monkeypatch):
    monkeypatch.setenv("MISSION_CONTROL_ENV", "development")
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "disabled")
    assert configure_runtime_auth("127.0.0.1", local_dev_no_auth=True) == "disabled"
    assert KeycloakVerifier().require(None, "mission:read").subject == "local-development"


def test_direct_keycloak_verifier_requires_explicit_disabled_mode(monkeypatch):
    monkeypatch.delenv("MISSION_CONTROL_AUTH_MODE", raising=False)
    monkeypatch.delenv("MISSION_CONTROL_JWT_ISSUER", raising=False)
    with pytest.raises(RuntimeError, match="JWT_ISSUER"):
        KeycloakVerifier()


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_security_mode_cannot_be_disabled_in_protected_environments(monkeypatch, environment):
    monkeypatch.setenv("MISSION_CONTROL_ENV", environment)
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "disabled")
    with pytest.raises(RuntimeError, match="cannot be disabled"):
        KeycloakVerifier()


def test_invalid_auth_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("MISSION_CONTROL_AUTH_MODE", "optional")
    with pytest.raises(RuntimeError, match="unknown"):
        KeycloakVerifier()
