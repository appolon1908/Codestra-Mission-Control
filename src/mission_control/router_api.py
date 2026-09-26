from __future__ import annotations

import hmac
import json
import os
import ipaddress
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .mission_router import (
    MissionRouter,
    RouterConflict,
    RouterLeaseError,
    RouterNotReady,
)

MAX_BODY = 1024 * 1024
TOKEN_ENV = "MISSION_CONTROL_API_TOKEN"
MIN_TOKEN_LENGTH = 32


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _api_token(host: str) -> str | None:
    token = os.environ.get(TOKEN_ENV)
    if token is not None and len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"{TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} characters")
    if not _is_loopback_host(host) and not token:
        raise ValueError(
            f"non-loopback router bind requires {TOKEN_ENV} with at least "
            f"{MIN_TOKEN_LENGTH} characters"
        )
    return token




class MissionRouterAPI:
    """HTTP API for dashboard reads, atomic-task claims/moves, and reconciliation."""

    def __init__(self, router: MissionRouter) -> None:
        self.router = router

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        router = self.router
        api_token = _api_token(host)

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/router-v1"

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

            def _body(self) -> dict | None:
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    return None
                if length <= 0 or length > MAX_BODY:
                    return None
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None
                return payload if isinstance(payload, dict) else None

            def _authorized(self, path: str) -> bool:
                if path == "/health" or not api_token:
                    return True
                header = self.headers.get("Authorization", "")
                prefix = "Bearer "
                if not header.startswith(prefix):
                    self._json(401, {"error": "unauthorized"})
                    return False
                candidate = header[len(prefix) :]
                if not hmac.compare_digest(candidate, api_token):
                    self._json(401, {"error": "unauthorized"})
                    return False
                return True

            def _handle_error(self, exc: Exception) -> None:
                if isinstance(exc, KeyError):
                    self._json(404, {"error": "not_found", "detail": str(exc)})
                elif isinstance(exc, (RouterConflict, RouterNotReady, RouterLeaseError)):
                    self._json(
                        409,
                        {"error": type(exc).__name__, "detail": str(exc)},
                    )
                elif isinstance(exc, ValueError):
                    self._json(400, {"error": "invalid_request", "detail": str(exc)})
                else:
                    self._json(
                        500,
                        {"error": "router_failure", "detail": f"{type(exc).__name__}: {exc}"},
                    )

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if not self._authorized(parsed.path):
                    return
                query = parse_qs(parsed.query)
                if parsed.path == "/health":
                    self._json(
                        200,
                        {"ok": True, "service": "mission-control-router-api"},
                    )
                    return
                if parsed.path == "/platform/v1/router/dashboard":
                    repository = (query.get("repository") or [None])[0]
                    self._json(200, router.dashboard(repository=repository))
                    return
                if parsed.path == "/platform/v1/router/tasks":
                    repository = (query.get("repository") or [None])[0]
                    work_state = (query.get("state") or [None])[0]
                    items = router.tasks(
                        repository=repository,
                        work_state=work_state,
                    )
                    self._json(200, {"count": len(items), "items": items})
                    return
                prefix = "/platform/v1/router/repositories/"
                suffix = "/hierarchy"
                if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
                    raw_repository = parsed.path[len(prefix) : -len(suffix)].rstrip("/")
                    repository = unquote(raw_repository)
                    items = router.hierarchy(repository)
                    self._json(
                        200,
                        {
                            "repository": repository,
                            "area_count": len(items),
                            "areas": items,
                        },
                    )
                    return
                task_prefix = "/platform/v1/router/tasks/"
                if parsed.path.startswith(task_prefix):
                    task_id = unquote(parsed.path[len(task_prefix) :]).strip("/")
                    if not task_id or "/" in task_id:
                        self._json(404, {"error": "not_found"})
                        return
                    try:
                        self._json(200, router.task(task_id))
                    except Exception as exc:  # noqa: BLE001
                        self._handle_error(exc)
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if not self._authorized(parsed.path):
                    return
                body = self._body()
                if body is None:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return
                try:
                    if parsed.path == "/platform/v1/router/move-agents":
                        agent_ids = [
                            str(value).strip()
                            for value in body.get("agent_ids") or []
                            if str(value).strip()
                        ]
                        if not agent_ids:
                            raise ValueError("agent_ids is required")
                        leases = router.move_agents(
                            agent_ids,
                            repository=body.get("repository"),
                            ttl_seconds=int(body.get("ttl_seconds", 600)),
                        )
                        self._json(
                            200,
                            {
                                "assigned": [asdict(lease) for lease in leases],
                                "assigned_count": len(leases),
                            },
                        )
                        return

                    if parsed.path == "/platform/v1/router/reconcile":
                        task_id = str(body.get("task_id") or "").strip()
                        head_sha = str(body.get("head_sha") or "").strip()
                        if not task_id or not head_sha:
                            raise ValueError("task_id and head_sha are required")
                        result = router.record_reconciliation(
                            task_id,
                            head_sha=head_sha,
                            pr_merged=bool(body.get("pr_merged", False)),
                            ci_green=bool(body.get("ci_green", False)),
                            post_merge_green=bool(body.get("post_merge_green", False)),
                            pr_url=body.get("pr_url"),
                            ci_url=body.get("ci_url"),
                            payload=dict(body.get("evidence") or {}),
                        )
                        self._json(200, result)
                        return

                    prefix = "/platform/v1/router/tasks/"
                    if parsed.path.startswith(prefix):
                        remainder = parsed.path[len(prefix) :].strip("/")
                        parts = remainder.split("/")
                        if len(parts) != 2:
                            self._json(404, {"error": "not_found"})
                            return
                        task_id, action = map(unquote, parts)
                        if action == "claim":
                            agent_id = str(body.get("agent_id") or "").strip()
                            if not agent_id:
                                raise ValueError("agent_id is required")
                            lease = router.claim_task(
                                task_id,
                                agent_id,
                                ttl_seconds=int(body.get("ttl_seconds", 600)),
                            )
                            self._json(201, asdict(lease))
                            return
                        if action == "heartbeat":
                            lease_token = str(body.get("lease_token") or "").strip()
                            if not lease_token:
                                raise ValueError("lease_token is required")
                            lease = router.heartbeat(
                                task_id,
                                lease_token,
                                ttl_seconds=int(body.get("ttl_seconds", 600)),
                            )
                            self._json(200, asdict(lease))
                            return
                        if action == "complete":
                            lease_token = str(body.get("lease_token") or "").strip()
                            head_sha = str(body.get("head_sha") or "").strip()
                            if not lease_token or not head_sha:
                                raise ValueError("lease_token and head_sha are required")
                            router.complete_work(task_id, lease_token, head_sha=head_sha)
                            self._json(200, router.task(task_id))
                            return

                    self._json(404, {"error": "not_found"})
                except Exception as exc:  # noqa: BLE001
                    self._handle_error(exc)

        return ThreadingHTTPServer((host, port), Handler)
