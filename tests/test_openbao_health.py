"""Read-only OpenBao health: fail-closed URL policy, safe output and API auth."""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from mission_control.openbao_health import probe


class FakeResponse(io.BytesIO):
    def __init__(self, status: int, body: bytes):
        super().__init__(body)
        self.status = status

    def getcode(self):
        return self.status


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout, dict(request.headers)))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


URL = "https://bao.example.test:8200/v1/sys/health"
ALLOWED = "bao.example.test"


def test_unconfigured_never_touches_network():
    opener = FakeOpener(FakeResponse(200, b'{"initialized":true,"sealed":false}'))
    assert probe(url="", allowed_hosts=ALLOWED, opener=opener) == {"state": "NOT_CONFIGURED"}
    assert probe(url=URL, allowed_hosts="", opener=opener) == {"state": "NOT_CONFIGURED"}
    assert opener.calls == []


@pytest.mark.parametrize("url,allowed", [
    ("http://bao.example.test:8200/v1/sys/health", ALLOWED),
    ("https://evil.example.test:8200/v1/sys/health", ALLOWED),
    ("https://bao.example.test.evil.test/v1/sys/health", ALLOWED),
    ("https://user:pass@bao.example.test/v1/sys/health", ALLOWED),
    ("https://bao.example.test/v1/sys/health?standbyok=true", ALLOWED),
    ("https://bao.example.test/v1/sys/health#private", ALLOWED),
    ("https://bao.example.test/v1/sys/unseal", ALLOWED),
    ("https://bao.example.test:9999/v1/sys/health", ALLOWED),
    ("https://bao.example.test/v1/sys/health", "evil.example.test"),
    ("https://bao.example.test:bad/v1/sys/health", ALLOWED),
])
def test_unapproved_endpoint_is_rejected_without_http_call(url, allowed):
    opener = FakeOpener(FakeResponse(200, b'{"initialized":true,"sealed":false}'))
    assert probe(url=url, allowed_hosts=allowed, opener=opener) == {"state": "CONFIG_REJECTED"}
    assert opener.calls == []


def test_success_extracts_only_the_reviewed_health_fields():
    body = json.dumps({
        "initialized": True, "sealed": False, "standby": False,
        "version": "secret-internal-build", "cluster_name": "private-cluster",
        "root_token": "secret-must-never-appear",
    }).encode()
    opener = FakeOpener(FakeResponse(200, body))
    value = probe(url=URL, allowed_hosts=ALLOWED, opener=opener)
    assert value["state"] == "OBSERVED"
    assert value["initialized"] is True
    assert value["sealed"] is False
    assert value["standby"] is False
    assert value["http_status"] == 200
    assert "secret" not in json.dumps(value).lower()
    assert len(opener.calls) == 1
    called_url, method, timeout, headers = opener.calls[0]
    assert called_url == URL
    assert method == "GET" and timeout == 3
    assert "Authorization" not in headers


def test_sealed_non_200_response_is_valid_read_only_observation():
    body = io.BytesIO(b'{"initialized":true,"sealed":true,"standby":false}')
    opener = FakeOpener(HTTPError(URL, 503, "sealed", {}, body))
    value = probe(url=URL, allowed_hosts=ALLOWED, opener=opener)
    assert value["state"] == "OBSERVED"
    assert value["http_status"] == 503
    assert value["sealed"] is True


@pytest.mark.parametrize("raw", [
    b"", b'{"sealed":false}', b'{"initialized":true,"sealed":"false"}',
    b'{"initialized":1,"sealed":false}', b'{"initialized":true,"sealed":false,"standby":"0"}',
    b'not-json', b'x' * 5000, b'["wrong shape"]',
])
def test_invalid_or_secret_shaped_response_is_never_forwarded(raw):
    result = probe(url=URL, allowed_hosts=ALLOWED, opener=FakeOpener(FakeResponse(200, raw)))
    assert result["state"] == "INVALID_RESPONSE" or result["state"] == "UNAVAILABLE"
    assert "initialized" not in result


def test_redirect_or_http_exception_is_not_followed():
    opener = FakeOpener(FakeResponse(302, b'{"initialized":true,"sealed":false}'))
    assert probe(url=URL, allowed_hosts=ALLOWED, opener=opener) == {"state": "UNAVAILABLE"}
    assert len(opener.calls) == 1
