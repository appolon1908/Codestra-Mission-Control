from __future__ import annotations

import json
import os
import socket
import urllib.request
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent_intelligence import AgentScoreInput, rate_agent
from .agent_metrics import AgentMetricsCollector
from .store import MissionStore

API_VERSION = "v1"
MAX_BODY_BYTES = 1_000_000


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class AgentTelemetryEmitter:
    def __init__(
        self,
        store: MissionStore,
        *,
        endpoint: str | None = None,
        timeout_seconds: float = 3.0,
        host_name: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.store = store
        self.endpoint = endpoint or os.getenv("MISSION_CONTROL_AGENT_TELEMETRY_URL")
        self.timeout_seconds = timeout_seconds
        self.host_name = host_name or socket.gethostname()
        self.api_token = api_token or os.getenv("MISSION_CONTROL_AGENT_API_TOKEN")

    def record_launch(
        self,
        assignment: Any,
        execution: Any,
        *,
        role: str = "WRITER",
        mission_level: int = 1,
        complexity_class: str = "C3",
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": str(uuid.uuid4()),
            "event_type": "AGENT_LAUNCHED",
            "created_at": utcnow_iso(),
            "execution_id": execution.execution_id,
            "mission_id": assignment.mission_id,
            "agent_id": assignment.agent_id,
            "provider": execution.provider,
            "role": role,
            "host": self.host_name,
            "repository": assignment.repository,
            "worktree": assignment.worktree,
            "branch": assignment.branch,
            "head_sha": assignment.base_sha,
            "mission_level": int(mission_level),
            "complexity_class": complexity_class,
            "state": execution.state,
        }
        self.store.record_agent_launch(payload)
        self.record_execution_event(execution, "AGENT_LAUNCHED", metadata=payload)
        if self.endpoint:
            self._post(payload)
        return payload

    def record_execution_event(
        self,
        execution: Any,
        event_type: str,
        *,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "created_at": utcnow_iso(),
            "execution_id": execution.execution_id,
            "mission_id": execution.mission_id,
            "agent_id": execution.agent_id,
            "provider": execution.provider,
            "state": execution.state,
            "reason": reason,
            "metadata": metadata or {},
        }
        self.store.record_agent_event(payload)
        return payload

    def _post(self, payload: dict[str, object]) -> None:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"telemetry endpoint returned HTTP {response.status}")


class AgentTelemetryAPI:
    def __init__(
        self,
        store: MissionStore,
        *,
        api_token: str | None = None,
    ) -> None:
        self.store = store
        self.api_token = api_token or os.getenv("MISSION_CONTROL_AGENT_API_TOKEN")

    def handler(self) -> type[BaseHTTPRequestHandler]:
        store = self.store
        collector = AgentMetricsCollector(store)
        api_token = self.api_token

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraAgentTelemetry/1.1"

            def _json(self, status: int, payload: object) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Codestra-API-Version", API_VERSION)
                self.send_header("X-Request-Id", str(uuid.uuid4()))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if not api_token:
                    return True
                expected = f"Bearer {api_token}"
                if self.headers.get("Authorization") == expected:
                    return True
                self._json(401, {"error": "unauthorized"})
                return False

            def _body(self) -> dict[str, object]:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise ValueError("invalid content length")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise TypeError("request body must be a JSON object")
                return value

            def _limit(self, parsed) -> int:
                query = parse_qs(parsed.query)
                return min(500, max(1, int(query.get("limit", ["100"])[0])))

            def _execution_identity_error(
                self,
                payload: dict[str, object],
                *,
                require_exists: bool = True,
            ) -> tuple[int, dict[str, object]] | None:
                execution_id = str(payload.get("execution_id") or "")
                if not execution_id:
                    return 400, {"error": "missing_fields", "fields": ["execution_id"]}
                execution = store.get_agent_execution(execution_id)
                identity = execution or store.get_agent_launch(execution_id)
                if not identity:
                    if require_exists:
                        return 404, {
                            "error": "execution_not_found",
                            "id": execution_id,
                        }
                    return None
                mismatches: dict[str, dict[str, str]] = {}
                for key in ("agent_id", "mission_id"):
                    if key in payload and str(payload[key]) != str(identity[key]):
                        mismatches[key] = {
                            "expected": str(identity[key]),
                            "received": str(payload[key]),
                        }
                if mismatches:
                    return 409, {
                        "error": "execution_identity_mismatch",
                        "id": execution_id,
                        "mismatches": mismatches,
                    }
                return None

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path in {"/healthz", "/readyz"}:
                    self._json(200, {"status": "ok", "api_version": API_VERSION})
                    return
                if parsed.path == "/metrics":
                    summary = store.agent_metrics_summary()
                    lines = [
                        "# TYPE codestra_agents_active gauge",
                        f"codestra_agents_active {summary['active_agents']}",
                        "# TYPE codestra_agent_launches_total counter",
                        f"codestra_agent_launches_total {summary['launches_total']}",
                        "# TYPE codestra_agent_stops_total counter",
                        f"codestra_agent_stops_total {summary['stops_total']}",
                        "# TYPE codestra_agent_failures_total counter",
                        f"codestra_agent_failures_total {summary['failures_total']}",
                    ]
                    body = ("\n".join(lines) + "\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Codestra-API-Version", API_VERSION)
                    self.end_headers()
                    self.wfile.write(body)
                    return
                try:
                    limit = self._limit(parsed)
                except ValueError:
                    self._json(400, {"error": "invalid_limit"})
                    return

                if parsed.path == "/platform/v1/agents/launch-events":
                    rows = [dict(row) for row in store.list_agent_launches(limit=limit)]
                    for row in rows:
                        row["payload"] = json.loads(row.pop("payload_json"))
                    self._json(200, {"count": len(rows), "items": rows})
                    return
                if parsed.path == "/platform/v1/agents/events":
                    rows = [dict(row) for row in store.list_agent_events(limit=limit)]
                    for row in rows:
                        row["payload"] = json.loads(row.pop("payload_json"))
                    self._json(200, {"count": len(rows), "items": rows})
                    return
                if parsed.path == "/platform/v1/agents/active":
                    rows = [dict(row) for row in store.list_active_agents()]
                    self._json(200, {"count": len(rows), "items": rows})
                    return
                if parsed.path == "/platform/v1/agents/ratings":
                    rows = [dict(row) for row in store.list_agent_ratings(limit=limit)]
                    for row in rows:
                        row["dimensions"] = json.loads(row.pop("dimensions_json"))
                    self._json(200, {"count": len(rows), "items": rows})
                    return
                if parsed.path == "/platform/v1/agents/work-metrics":
                    rows = [dict(row) for row in store.list_agent_work_metrics(limit=limit)]
                    self._json(200, {"count": len(rows), "items": rows})
                    return
                if parsed.path == "/platform/v1/agents/summary":
                    self._json(200, store.agent_metrics_summary())
                    return
                if parsed.path == "/platform/v1/agents/leaderboard":
                    self._json(200, {"items": collector.leaderboard(limit=limit)})
                    return
                if parsed.path == "/platform/v1/agents/stale":
                    query = parse_qs(parsed.query)
                    try:
                        minutes = max(1.0, float(query.get("minutes", ["30"])[0]))
                    except ValueError:
                        self._json(400, {"error": "invalid_minutes"})
                        return
                    items = collector.stale(minutes=minutes)
                    self._json(200, {"count": len(items), "items": items})
                    return
                if parsed.path == "/platform/v1/agents/contract":
                    self._json(200, {
                        "api_version": API_VERSION,
                        "endpoints": [
                            "GET /platform/v1/agents/active",
                            "GET /platform/v1/agents/stale",
                            "GET /platform/v1/agents/leaderboard",
                            "GET /platform/v1/agents/executions/{execution_id}",
                            "GET /platform/v1/agents/missions/{mission_id}",
                            "GET /platform/v1/agents/publication/{execution_id}",
                            "GET /platform/v1/agents/reliability/{execution_id}",
                            "GET /platform/v1/agents/velocity/{execution_id}",
                            "GET /platform/v1/agents/goalposts/{execution_id}",
                            "POST /platform/v1/agents/refresh/{execution_id}",
                        ],
                    })
                    return
                prefixes = {
                    "/platform/v1/agents/executions/": "execution",
                    "/platform/v1/agents/missions/": "mission",
                    "/platform/v1/agents/publication/": "publication",
                    "/platform/v1/agents/reliability/": "reliability",
                    "/platform/v1/agents/velocity/": "velocity",
                    "/platform/v1/agents/goalposts/": "goalposts",
                }
                for prefix, kind in prefixes.items():
                    if parsed.path.startswith(prefix):
                        identifier = parsed.path[len(prefix):]
                        if not identifier:
                            self._json(400, {"error": "missing_identifier"})
                            return
                        try:
                            if kind == "execution":
                                value = collector.execution_detail(identifier)
                            elif kind == "mission":
                                if not store.get_mission(identifier):
                                    self._json(
                                        404,
                                        {"error": "mission_not_found", "id": identifier},
                                    )
                                    return
                                rows = [
                                    dict(row)
                                    for row in store.mission_agent_executions(identifier)
                                ]
                                value = {
                                    "mission_id": identifier,
                                    "count": len(rows),
                                    "items": rows,
                                }
                            elif kind == "publication":
                                value = collector.publication(identifier)
                            elif kind == "reliability":
                                value = collector.reliability(identifier)
                            elif kind == "velocity":
                                value = collector.velocity(identifier)
                            else:
                                value = collector.goalposts(identifier)
                        except KeyError:
                            self._json(404, {"error": "execution_not_found", "id": identifier})
                            return
                        self._json(200, value)
                        return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                parsed = urlparse(self.path)
                try:
                    payload = self._body()
                    if parsed.path == "/platform/v1/agents/launch-events":
                        required = {
                            "event_id", "execution_id", "mission_id", "agent_id",
                            "provider", "role", "host", "repository", "worktree",
                            "branch", "head_sha", "mission_level", "complexity_class",
                            "state",
                        }
                        missing = sorted(required.difference(payload))
                        if missing:
                            self._json(400, {"error": "missing_fields", "fields": missing})
                            return
                        identity_error = self._execution_identity_error(
                            payload, require_exists=False
                        )
                        if identity_error:
                            status, body = identity_error
                            self._json(status, body)
                            return
                        payload.setdefault("event_type", "AGENT_LAUNCHED")
                        payload.setdefault("created_at", utcnow_iso())
                        store.record_agent_launch(payload)
                        store.record_agent_event(payload)
                        self._json(201, payload)
                        return

                    if parsed.path == "/platform/v1/agents/events":
                        required = {
                            "event_id", "execution_id", "mission_id", "agent_id",
                            "provider", "event_type", "state",
                        }
                        missing = sorted(required.difference(payload))
                        if missing:
                            self._json(400, {"error": "missing_fields", "fields": missing})
                            return
                        identity_error = self._execution_identity_error(
                            payload, require_exists=False
                        )
                        if identity_error:
                            status, body = identity_error
                            self._json(status, body)
                            return
                        payload.setdefault("created_at", utcnow_iso())
                        store.record_agent_event(payload)
                        self._json(201, payload)
                        return

                    if parsed.path == "/platform/v1/agents/ratings":
                        required = {
                            "execution_id", "agent_id", "mission_id", "delivery",
                            "quality", "reliability", "goal_advancement", "speed",
                            "efficiency",
                        }
                        missing = sorted(required.difference(payload))
                        if missing:
                            self._json(400, {"error": "missing_fields", "fields": missing})
                            return
                        identity_error = self._execution_identity_error(payload)
                        if identity_error:
                            status, body = identity_error
                            self._json(status, body)
                            return
                        rating = rate_agent(
                            AgentScoreInput(
                                delivery=float(payload["delivery"]),
                                quality=float(payload["quality"]),
                                reliability=float(payload["reliability"]),
                                goal_advancement=float(payload["goal_advancement"]),
                                speed=float(payload["speed"]),
                                efficiency=float(payload["efficiency"]),
                                complexity_class=str(payload.get("complexity_class", "C3")),
                                evidence_penalty=float(payload.get("evidence_penalty", 0)),
                                confidence=float(payload.get("confidence", 100)),
                            )
                        )
                        stored = {
                            "execution_id": str(payload["execution_id"]),
                            "agent_id": str(payload["agent_id"]),
                            "mission_id": str(payload["mission_id"]),
                            **rating,
                        }
                        store.upsert_agent_rating(stored)
                        self._json(201, stored)
                        return

                    if parsed.path.startswith("/platform/v1/agents/refresh/"):
                        execution_id = parsed.path.removeprefix("/platform/v1/agents/refresh/")
                        if not execution_id:
                            self._json(400, {"error": "missing_identifier"})
                            return
                        try:
                            value = collector.refresh(execution_id)
                        except KeyError:
                            self._json(404, {"error": "execution_not_found", "id": execution_id})
                            return
                        self._json(200, value)
                        return

                    if parsed.path == "/platform/v1/agents/work-metrics":
                        required = {"execution_id", "agent_id", "mission_id"}
                        missing = sorted(required.difference(payload))
                        if missing:
                            self._json(400, {"error": "missing_fields", "fields": missing})
                            return
                        identity_error = self._execution_identity_error(payload)
                        if identity_error:
                            status, body = identity_error
                            self._json(status, body)
                            return
                        store.upsert_agent_work_metrics(payload)
                        self._json(201, payload)
                        return

                    self._json(404, {"error": "not_found"})
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    self._json(400, {"error": "invalid_request", "detail": str(exc)})

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        loopback_hosts = {"127.0.0.1", "::1", "localhost"}
        if host not in loopback_hosts and not self.api_token:
            raise ValueError(
                "MISSION_CONTROL_AGENT_API_TOKEN is required for non-loopback bind"
            )
        return ThreadingHTTPServer((host, port), self.handler())
