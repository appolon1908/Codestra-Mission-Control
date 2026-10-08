"""Bounded, permissioned, read-only OpenBao system health probe.

Never accepts client-supplied URLs, credentials, tokens, or arbitrary paths.
"""
from __future__ import annotations

import json
import os
import re
import ssl
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(*, url: str | None = None, allowed_hosts: str | None = None, opener=None) -> dict:
    """An explicit GET only. Missing/mismatched configuration fails closed."""
    if url is None:
        url = os.getenv("OPENBAO_HEALTH_URL", "")
    if allowed_hosts is None:
        allowed_hosts = os.getenv("OPENBAO_HEALTH_ALLOWED_HOSTS", "")
    if not url or not allowed_hosts:
        return {"state": "NOT_CONFIGURED"}
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        allowed = {item.strip().lower() for item in allowed_hosts.split(",") if item.strip()}
        if (
            parsed.scheme != "https"
            or not host or not re.fullmatch(r"[a-z0-9.-]+", host)
            or host.lower() not in allowed
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path != "/v1/sys/health"
            or parsed.port not in (None, 443, 8200)
        ):
            return {"state": "CONFIG_REJECTED"}
    except ValueError:
        return {"state": "CONFIG_REJECTED"}

    request = Request(url, method="GET", headers={"Accept": "application/json"})
    client = opener if opener is not None else build_opener(
        _NoRedirect(), HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        try:
            response = client.open(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            status = response.getcode()
            if status not in (200, 429, 501, 503):
                return {"state": "UNAVAILABLE"}
            raw = response.read(4097)
        if len(raw) > 4096:
            return {"state": "INVALID_RESPONSE"}
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or type(payload.get("initialized")) is not bool
            or type(payload.get("sealed")) is not bool
        ):
            return {"state": "INVALID_RESPONSE"}
        standby = payload.get("standby")
        if standby is not None and type(standby) is not bool:
            return {"state": "INVALID_RESPONSE"}
        return {
            "state": "OBSERVED",
            "http_status": status,
            "initialized": payload["initialized"],
            "sealed": payload["sealed"],
            "standby": standby,
            "observed_at": datetime.now(UTC).isoformat(),
        }
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return {"state": "UNAVAILABLE"}
