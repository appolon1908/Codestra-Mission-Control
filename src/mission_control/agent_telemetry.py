from __future__ import annotations

import json
import os
import socket
import urllib.request
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .adapters.base import AgentAssignment, AgentExecution
from .store import MissionStore


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
    ) -> None:
        self.store = store
        self.endpoint = endpoint or os.getenv("MISSION_CONTROL_AGENT_TELEMETRY_URL")
        self.timeout_seconds = timeout_seconds
        self.host_name = host_name or socket.gethostname()

    def record_launch(
        self,
        assignment: AgentAssignment,
        execution: AgentExecution,
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
        if self.endpoint:
            self._post(payload)
        return payload

    def _post(self, payload: dict[str, object]) -> None:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"telemetry endpoint returned HTTP {response.status}")


class AgentTelemetryAPI:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def handler(self) -> type[BaseHTTPRequestHandler]:
        store = self.store

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraAgentTelemetry/1.0"

            def _json(self, status: int, payload: object) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/healthz":
                    self._json(200, {"status": "ok"})
                    return
                if parsed.path == "/platform/v1/agents/launch-events":
                    query = parse_qs(parsed.query)
                    try:
                        limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
                    except ValueError:
                        self._json(400, {"error": "limit must be an integer"})
                        return
                    rows = [dict(row) for row in store.list_agent_launches(limit=limit)]
                    for row in rows:
                        row["payload"] = json.loads(row.pop("payload_json"))
                    self._json(200, {"count": len(rows), "items": rows})
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != "/platform/v1/agents/launch-events":
                    self._json(404, {"error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 1_000_000:
                        raise ValueError("invalid content length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    required = {
                        "event_id",
                        "execution_id",
                        "mission_id",
                        "agent_id",
                        "provider",
                        "role",
                        "host",
                        "repository",
                        "worktree",
                        "branch",
                        "head_sha",
                        "mission_level",
                        "complexity_class",
                        "state",
                    }
                    missing = sorted(required.difference(payload))
                    if missing:
                        self._json(400, {"error": "missing_fields", "fields": missing})
                        return
                    payload.setdefault("event_type", "AGENT_LAUNCHED")
                    payload.setdefault("created_at", utcnow_iso())
                    store.record_agent_launch(payload)
                    self._json(201, payload)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._json(400, {"error": "invalid_request", "detail": str(exc)})

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        return ThreadingHTTPServer((host, port), self.handler())
