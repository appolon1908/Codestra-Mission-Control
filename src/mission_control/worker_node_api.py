from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .store import MissionStore
from .worker_node import NodeNotFound, NodeValidationError, WorkerNodeRegistry

MAX_BODY = 64 * 1024
PREFIX = "/platform/v1/worker-nodes"

ENDPOINTS = (
    "GET /health",
    f"GET {PREFIX}",
    f"GET {PREFIX}/{{node_id}}",
    f"PUT {PREFIX}/{{node_id}}",
    f"POST {PREFIX}/{{node_id}}/heartbeat",
    f"GET {PREFIX}/{{node_id}}/readiness",
    f"GET {PREFIX}/{{node_id}}/provider-auth",
    f"PUT {PREFIX}/{{node_id}}/provider-auth/{{provider}}",
)


def _route(path: str) -> list[str] | None:
    """Split a worker-node path into segments after the prefix, or None."""
    if path == PREFIX:
        return []
    if not path.startswith(PREFIX + "/"):
        return None
    parts = [unquote(part) for part in path[len(PREFIX) + 1 :].split("/")]
    if any(not part for part in parts):
        return None
    return parts


class WorkerNodeAPI:
    def __init__(self, store: MissionStore) -> None:
        self.registry = WorkerNodeRegistry(store)

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        registry = self.registry

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/worker-node-v1"

            def log_message(self, fmt: str, *args) -> None:
                return

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(raw)

            def _body(self, *, allow_empty: bool = False) -> dict | None:
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    return None
                if length == 0 and allow_empty:
                    return {}
                if length <= 0 or length > MAX_BODY:
                    return None
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None
                return body if isinstance(body, dict) else None

            def _not_found(self, node_id: str) -> None:
                self._json(404, {"error": "node_not_found", "node_id": node_id})

            def _invalid(self, exc: NodeValidationError) -> None:
                self._json(422, {"error": "invalid_worker_node", "errors": exc.errors})

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/health":
                    self._json(200, {"ok": True, "service": "mission-control-worker-node-api"})
                    return
                parts = _route(path)
                try:
                    if parts == []:
                        items = registry.list()
                        self._json(200, {"count": len(items), "items": items})
                        return
                    if parts and len(parts) == 1:
                        self._json(200, registry.snapshot(parts[0]))
                        return
                    if parts and len(parts) == 2 and parts[1] == "readiness":
                        self._json(200, registry.readiness(parts[0]).as_payload())
                        return
                    if parts and len(parts) == 2 and parts[1] == "provider-auth":
                        items = [
                            status.as_payload()
                            for _, status in sorted(registry.auth(parts[0]).items())
                        ]
                        if not items and not registry.store.get_worker_node(parts[0]):
                            raise NodeNotFound(parts[0])
                        self._json(200, {"node_id": parts[0], "items": items})
                        return
                except NodeNotFound:
                    self._not_found(parts[0])
                    return
                self._json(404, {"error": "not_found"})

            def do_PUT(self) -> None:
                parts = _route(urlparse(self.path).path)
                if not parts or len(parts) not in (1, 3):
                    self._json(404, {"error": "not_found"})
                    return
                if len(parts) == 3 and parts[1] != "provider-auth":
                    self._json(404, {"error": "not_found"})
                    return
                body = self._body()
                if body is None:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return
                try:
                    if len(parts) == 1:
                        existed = registry.store.get_worker_node(parts[0]) is not None
                        snapshot = registry.register(parts[0], body)
                        self._json(200 if existed else 201, snapshot)
                        return
                    status = registry.record_auth(parts[0], parts[2], body)
                    self._json(200, status.as_payload())
                except NodeValidationError as exc:
                    self._invalid(exc)
                except NodeNotFound:
                    self._not_found(parts[0])

            def do_POST(self) -> None:
                parts = _route(urlparse(self.path).path)
                if not parts or len(parts) != 2 or parts[1] != "heartbeat":
                    self._json(404, {"error": "not_found"})
                    return
                if self._body(allow_empty=True) is None:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return
                try:
                    at = registry.heartbeat(parts[0])
                except NodeNotFound:
                    self._not_found(parts[0])
                    return
                decision = registry.readiness(parts[0])
                self._json(
                    200,
                    {
                        "node_id": parts[0],
                        "last_heartbeat_at": at,
                        "readiness": decision.as_payload(),
                    },
                )

        return ThreadingHTTPServer((host, port), Handler)
