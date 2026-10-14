from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .watchdog import WatchdogMonitor, escalation_payload

MAX_BODY = 64 * 1024
PREFIX = "/platform/v1/watchdog"
ESCALATION_STATES = ("OPEN", "ACKNOWLEDGED", "RESOLVED")
LEASE_STATES = ("ACTIVE", "STALE", "EXPIRED")

ENDPOINTS = (
    "GET /health",
    f"GET {PREFIX}/snapshot",
    f"GET {PREFIX}/workers",
    f"GET {PREFIX}/leases",
    f"GET {PREFIX}/blocked-lanes",
    f"GET {PREFIX}/escalations",
    f"POST {PREFIX}/escalations/evaluate",
    f"POST {PREFIX}/escalations/{{id}}/acknowledge",
    f"GET {PREFIX}/dispatches",
    f"POST {PREFIX}/tick",
)


def _csv(query: dict[str, list[str]], key: str) -> list[str]:
    return [
        part.strip().upper()
        for value in query.get(key, [])
        for part in value.split(",")
        if part.strip()
    ]


def _flag(query: dict[str, list[str]], key: str) -> bool:
    return (query.get(key) or ["0"])[-1].lower() in {"1", "true", "yes"}


class WatchdogAPI:
    """Loopback runtime API for watchdog readback and escalation handling.

    Dispatch through POST /tick is only possible when a scheduler is attached and
    allow_dispatch is set; otherwise the API is read/acknowledge only.
    """

    def __init__(
        self,
        monitor: WatchdogMonitor,
        *,
        scheduler: object | None = None,
        allow_dispatch: bool = False,
    ) -> None:
        self.monitor = monitor
        self.scheduler = scheduler
        self.allow_dispatch = allow_dispatch

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/watchdog-v1"

            def log_message(self, fmt: str, *args) -> None:
                return

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> dict | None:
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    return None
                if length == 0:
                    return {}
                if length < 0 or length > MAX_BODY:
                    return None
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None
                return body if isinstance(body, dict) else None

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                query = parse_qs(parsed.query)
                monitor = api.monitor

                if path == "/health":
                    self._json(
                        200,
                        {
                            "ok": True,
                            "service": "mission-control-watchdog-api",
                            "dispatch_enabled": api.scheduler is not None and api.allow_dispatch,
                            "external_delivery": "DISABLED",
                            "endpoints": list(ENDPOINTS),
                        },
                    )
                    return
                if path == f"{PREFIX}/snapshot":
                    self._json(200, monitor.snapshot())
                    return
                if path == f"{PREFIX}/workers":
                    self._json(200, monitor.active_workers())
                    return
                if path == f"{PREFIX}/leases":
                    states = _csv(query, "state") or ["STALE", "EXPIRED"]
                    invalid = sorted(set(states) - set(LEASE_STATES))
                    if invalid:
                        self._json(400, {"error": "invalid_state", "states": invalid})
                        return
                    items = [lease for lease in monitor.lease_states() if lease["state"] in states]
                    self._json(200, {"count": len(items), "states": states, "items": items})
                    return
                if path == f"{PREFIX}/blocked-lanes":
                    items = monitor.blocked_lanes()
                    self._json(200, {"count": len(items), "items": items})
                    return
                if path == f"{PREFIX}/escalations":
                    states = _csv(query, "state") or ["OPEN", "ACKNOWLEDGED"]
                    invalid = sorted(set(states) - set(ESCALATION_STATES))
                    if invalid:
                        self._json(400, {"error": "invalid_state", "states": invalid})
                        return
                    mission_id = (query.get("mission_id") or [None])[-1]
                    items = [
                        escalation_payload(row)
                        for row in monitor.store.list_watchdog_escalations(
                            states=tuple(states),
                            mission_id=mission_id,
                        )
                    ]
                    self._json(200, {"count": len(items), "states": states, "items": items})
                    return
                if path == f"{PREFIX}/dispatches":
                    try:
                        limit = int((query.get("limit") or ["100"])[-1])
                    except ValueError:
                        self._json(400, {"error": "invalid_limit"})
                        return
                    items = monitor.dispatches(
                        mission_id=(query.get("mission_id") or [None])[-1],
                        takeover_only=_flag(query, "takeover"),
                        limit=limit,
                    )
                    self._json(200, {"count": len(items), "items": items})
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/")
                body = self._body()
                if body is None:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return

                if path == f"{PREFIX}/escalations/evaluate":
                    self._json(200, api.monitor.evaluate_escalations())
                    return

                prefix = f"{PREFIX}/escalations/"
                suffix = "/acknowledge"
                if path.startswith(prefix) and path.endswith(suffix):
                    raw_id = path[len(prefix) : -len(suffix)]
                    if not raw_id.isdigit():
                        self._json(404, {"error": "not_found"})
                        return
                    actor = str(body.get("actor", "")).strip()
                    if not actor:
                        self._json(400, {"error": "missing_fields", "fields": ["actor"]})
                        return
                    try:
                        row = api.monitor.store.acknowledge_watchdog_escalation(
                            int(raw_id),
                            actor,
                        )
                    except KeyError:
                        self._json(
                            404,
                            {"error": "escalation_not_found", "escalation_id": int(raw_id)},
                        )
                        return
                    except ValueError as exc:
                        self._json(409, {"error": "escalation_resolved", "detail": str(exc)})
                        return
                    self._json(200, escalation_payload(row))
                    return

                if path == f"{PREFIX}/tick":
                    if api.scheduler is None:
                        self._json(503, {"error": "scheduler_unavailable"})
                        return
                    if body.get("dispatch") is not True:
                        self._json(
                            400,
                            {
                                "error": "dispatch_confirmation_required",
                                "detail": 'send {"dispatch": true} to run a scheduler tick',
                            },
                        )
                        return
                    if not api.allow_dispatch:
                        self._json(403, {"error": "dispatch_disabled"})
                        return
                    snapshot = api.scheduler.tick()  # type: ignore[attr-defined]
                    self._json(200, asdict(snapshot))
                    return

                self._json(404, {"error": "not_found"})

        return ThreadingHTTPServer((host, port), Handler)
