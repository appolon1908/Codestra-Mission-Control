"""Synthetic staging-chain acceptance; no network, secrets, or effects."""
from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "readonly_staging_probe", ROOT / "scripts/probe_staging_auth_path.py"
)
assert SPEC and SPEC.loader
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def transport(*, override: dict | None = None):
    calls = []
    changes = override or {}

    def mock(url, token):
        calls.append((url, token))
        if url in changes:
            return changes[url]
        if url == PROBE.ISSUER + "/.well-known/openid-configuration":
            return 200, {
                "issuer": PROBE.ISSUER,
                "jwks_uri": PROBE.ISSUER + "/protocol/openid-connect/certs",
            }, {}
        if url == PROBE.PREFIX + "/contract" and token is None:
            return 401, {"error_code": "missing_bearer_token"}, {}
        if url == PROBE.PREFIX + "/contract" and token != "aaa.bbb.ccc":
            return 401, {"error_code": "invalid_bearer_token"}, {}
        if url == PROBE.PREFIX + "/contract":
            return 200, {"endpoints": {"tasks": {}}}, {"Cache-Control": "no-store"}
        if url == PROBE.PREFIX + "/repositories":
            return 200, {"repositories": [{"repository": "Middleware-"}]}, {}
        if url == PROBE.PREFIX + "/agents":
            return 200, {"agents": []}, {}
        if url == PROBE.PREFIX + "/sources":
            return 200, {"sources": {"prs": "UNVERIFIED"}}, {}
        if url == PROBE.PREFIX + "/notifications":
            return 200, {"notifications": []}, {}
        if url.startswith(PROBE.PREFIX + "/repository?"):
            return 200, {"repository": {"repository": "Middleware-"}}, {}
        if url.startswith(PROBE.PREFIX + "/tasks?"):
            return 200, {"tasks": [{"task_id": "synthetic-task-1"}]}, {}
        if url.startswith(PROBE.PREFIX + "/local-work?"):
            return 200, {"lanes": []}, {}
        if url.startswith(PROBE.PREFIX + "/task?"):
            return 200, {"task": {"task_id": "synthetic-task-1"}}, {}
        if url in (
            PROBE.PREFIX + "/unsupported-unknown",
            PROBE.BASE + "/metrics",
            PROBE.BASE + "/internal",
        ):
            return 404, {"error": "not_found"}, {}
        raise AssertionError("Unexpected test URL: " + url)

    return mock, calls


def test_exact_nine_read_only_paths_are_checked():
    http, calls = transport()
    result = PROBE.verify(http, "aaa.bbb.ccc")
    assert result["http_probe"] == "PASS"
    assert result["read_routes_checked"] == 9
    assert result["staging_promotion_go"] == "NO"
    assert result["production_go"] == "NO"
    assert result["runtime_exact_sha_certified"] is False
    assert len(calls) == 15
    assert not any(u.endswith("/admin") for u, _ in calls)
    assert all(u.startswith("https://") for u, _ in calls)


def test_missing_or_malformed_bearer_fails_before_io():
    http, calls = transport()
    for token in ("", "invalid", "not jwt", "a.b.c\n"):
        with pytest.raises(PROBE.ProbeFailure, match="JWT_REQUIRED"):
            PROBE.verify(http, token)
    assert calls == []


def test_oidc_discovery_wrong_issuer_is_rejected():
    http, _ = transport(override={
        PROBE.ISSUER + "/.well-known/openid-configuration": (
            200, {"issuer": "https://wrong.invalid", "jwks_uri": PROBE.ISSUER + "/jwks"}, {}
        )
    })
    with pytest.raises(PROBE.ProbeFailure, match="ISSUER_MISMATCH"):
        PROBE.verify(http, "aaa.bbb.ccc")


def test_anonymous_or_invalid_token_pass_through_is_rejected():
    http, _ = transport(override={
        PROBE.PREFIX + "/contract": (200, {"endpoints": {}}, {"Cache-Control": "no-store"})
    })
    with pytest.raises(PROBE.ProbeFailure, match="STATUS_MISMATCH"):
        PROBE.verify(http, "aaa.bbb.ccc")


def test_missing_postgresql_tasks_cannot_certify():
    http, _ = transport(override={
        PROBE.PREFIX + "/tasks?repository=Middleware-": (200, {"tasks": []}, {})
    })
    with pytest.raises(PROBE.ProbeFailure, match="TASKS_MISSING"):
        PROBE.verify(http, "aaa.bbb.ccc")


def test_private_endpoint_exposure_is_a_failure():
    http, _ = transport(override={
        PROBE.BASE + "/metrics": (200, {"metrics": "should be private"}, {})
    })
    with pytest.raises(PROBE.ProbeFailure, match="STATUS_MISMATCH"):
        PROBE.verify(http, "aaa.bbb.ccc")


def test_main_missing_token_never_checks_network(monkeypatch, capsys):
    monkeypatch.delenv("MC_STAGING_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(PROBE.socket, "getaddrinfo", lambda *args: pytest.fail("network called"))
    assert PROBE.main() == 2
    assert "JWT_REQUIRED" in capsys.readouterr().err


def test_main_unresolved_private_dns_returns_blocked(monkeypatch, capsys):
    monkeypatch.setenv("MC_STAGING_BEARER_TOKEN", "aaa.bbb.ccc")
    def no_dns(*args):
        raise socket.gaierror("missing private record")
    monkeypatch.setattr(PROBE.socket, "getaddrinfo", no_dns)
    assert PROBE.main() == 2
    assert "PRIVATE_DNS_UNRESOLVED" in capsys.readouterr().err


def test_transport_contains_no_write_actions():
    source = (ROOT / "scripts/probe_staging_auth_path.py").read_text()
    assert 'Request(url, headers=headers, method="GET")' in source
    assert 'method="POST"' not in source
    assert "MC_STAGING_CA_FILE" in source
    assert "ssl.create_default_context" in source
    assert "NoRedirects()" in source
