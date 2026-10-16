#!/usr/bin/env python3
"""Read-only, TLS-enforced Mission Control staging chain acceptance probe.

Requires a short-lived Keycloak access token in MC_STAGING_BEARER_TOKEN.
Never logs token contents or sends modifying HTTP requests. This is a staging
HTTP probe, NOT deployment authorization or production certification.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

BASE = "https://dashboard.staging.internal.codestra.agency"
ISSUER = "https://auth-staging.codestra.co/realms/codestra"
PREFIX = BASE + "/platform/v1/dashboard"
ROUTES = (
    "contract", "repositories", "repository", "agents", "tasks",
    "task", "local-work", "sources", "notifications",
)


class ProbeFailure(RuntimeError):
    """Fail closed without surfacing bearer token contents."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def make_transport(ca_path: str | None = None):
    context = ssl.create_default_context(cafile=ca_path or None)
    opener = build_opener(HTTPSHandler(context=context), NoRedirects())

    def read(url: str, token: str | None):
        if not (url.startswith(BASE + "/") or url.startswith(ISSUER + "/")):
            raise ProbeFailure("UNAPPROVED_OUTBOUND_TARGET")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(url, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=7) as response:
                status = response.status
                data = response.read(524288)
                received = dict(response.headers)
        except HTTPError as exc:
            status = exc.code
            data = exc.read(524288)
            received = dict(exc.headers)
        except (URLError, OSError, TimeoutError) as exc:
            raise ProbeFailure("TLS_OR_NETWORK_UNAVAILABLE") from exc
        try:
            payload = json.loads(data) if data else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProbeFailure("INVALID_JSON_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise ProbeFailure("INVALID_JSON_RESPONSE")
        return status, payload, received

    return read


def verify(transport: Callable, token: str) -> dict:
    if not token or token.count(".") != 2 or any(c in token for c in "\r\n "):
        raise ProbeFailure("JWT_REQUIRED")
    checked: list[str] = []

    def request(path: str, *, bearer: str | None = token, expected: int = 200):
        status, body, headers = transport(path, bearer)
        if status != expected:
            raise ProbeFailure("STATUS_MISMATCH:" + path.replace(BASE, "")[:100] + ":" + str(status))
        return body, headers

    discovery, _ = request(ISSUER + "/.well-known/openid-configuration", bearer=None)
    if discovery.get("issuer") != ISSUER:
        raise ProbeFailure("ISSUER_MISMATCH")
    if not str(discovery.get("jwks_uri", "")).startswith(ISSUER + "/"):
        raise ProbeFailure("JWKS_AUTHORITY_MISMATCH")
    checked.append("oidc-discovery")

    request(PREFIX + "/contract", bearer=None, expected=401)
    request(PREFIX + "/contract", bearer="tampered.signature.rejected", expected=401)
    checked.extend(("unauthenticated-denied", "invalid-token-denied"))

    contract, contract_headers = request(PREFIX + "/contract")
    if "endpoints" not in contract or "no-store" not in contract_headers.get("Cache-Control", ""):
        raise ProbeFailure("CONTRACT_OR_CACHE_CONTROL_MISSING")
    checked.append("contract")

    repos, _ = request(PREFIX + "/repositories")
    records = repos.get("repositories")
    if not isinstance(records, list) or not records:
        raise ProbeFailure("REPOSITORIES_MISSING")
    first = records[0]
    if not isinstance(first, dict) or not isinstance(first.get("repository"), str):
        raise ProbeFailure("REPOSITORY_ID_MISSING")
    repo = quote(first["repository"], safe="")
    checked.append("repositories")

    agents, _ = request(PREFIX + "/agents")
    if not isinstance(agents.get("agents"), list):
        raise ProbeFailure("AGENTS_INVALID")
    checked.append("agents")

    sources, _ = request(PREFIX + "/sources")
    if not isinstance(sources.get("sources"), dict):
        raise ProbeFailure("SOURCES_INVALID")
    checked.append("sources")

    notes, _ = request(PREFIX + "/notifications")
    if not isinstance(notes.get("notifications"), (list, dict)):
        raise ProbeFailure("NOTIFICATIONS_INVALID")
    checked.append("notifications")

    request(PREFIX + "/repository?repository=" + repo)
    checked.append("repository")
    tasks, _ = request(PREFIX + "/tasks?repository=" + repo)
    if not isinstance(tasks.get("tasks"), list) or not tasks["tasks"]:
        raise ProbeFailure("TASKS_MISSING")
    checked.append("tasks")
    work, _ = request(PREFIX + "/local-work?repository=" + repo + "&recent_hours=48")
    if not isinstance(work.get("lanes"), list):
        raise ProbeFailure("LOCAL_WORK_INVALID")
    checked.append("local-work")

    task = tasks["tasks"][0]
    if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
        raise ProbeFailure("TASK_ID_MISSING")
    task_resp, _ = request(PREFIX + "/task?task_id=" + quote(task["task_id"], safe=""))
    if not isinstance(task_resp.get("task"), dict):
        raise ProbeFailure("TASK_DETAIL_INVALID")
    checked.append("task")

    for path in (PREFIX + "/unsupported-unknown", BASE + "/metrics", BASE + "/internal"):
        request(path, expected=404)
    checked.append("private-and-unknown-denied")

    return {
        "http_probe": "PASS",
        "read_routes_checked": len(ROUTES),
        "checks": checked,
        "real_staging_tls": True,
        "runtime_exact_sha_certified": False,
        "staging_promotion_go": "NO",
        "production_go": "NO",
    }


def main() -> int:
    token = os.environ.get("MC_STAGING_BEARER_TOKEN", "")
    if not token or token.count(".") != 2:
        print("STAGING_HTTP_PROBE=BLOCKED JWT_REQUIRED", file=sys.stderr)
        return 2
    try:
        socket.getaddrinfo("dashboard.staging.internal.codestra.agency", 443)
    except socket.gaierror:
        print("STAGING_HTTP_PROBE=BLOCKED PRIVATE_DNS_UNRESOLVED", file=sys.stderr)
        return 2
    try:
        results = verify(make_transport(os.environ.get("MC_STAGING_CA_FILE")), token)
    except (ProbeFailure, OSError, ssl.SSLError) as exc:
        code = exc.args[0] if isinstance(exc, ProbeFailure) else "TLS_OR_NETWORK_UNAVAILABLE"
        print("STAGING_HTTP_PROBE=FAIL " + str(code)[:140], file=sys.stderr)
        return 1
    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
