from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
import urllib.request
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
        payload["delivery_state"] = "LOCAL_ONLY"
        self.store.record_agent_launch(payload)
        if self.endpoint:
            try:
                self._post(payload)
            except Exception as exc:  # noqa: BLE001 - telemetry must not break dispatch
                payload["delivery_state"] = "FAILED"
                payload["delivery_error_type"] = type(exc).__name__
                self.store.record_agent_launch(payload)
            else:
                payload["delivery_state"] = "DELIVERED"
                self.store.record_agent_launch(payload)
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
    def __init__(self, store: MissionStore, *, bearer_token: str | None = None) -> None:
        self.store = store
        self.bearer_token = bearer_token

    def handler(self) -> type[BaseHTTPRequestHandler]:
        store = self.store
        bearer_token = self.bearer_token

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraAgentTelemetry/1.0"

            def _json(self, status: int, payload: object) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Request-ID", str(uuid.uuid4()))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if bearer_token is None:
                    return True
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {bearer_token}"
                return hmac.compare_digest(supplied, expected)

            def _require_authorized(self) -> bool:
                if self._authorized():
                    return True
                self._json(401, {"error": "unauthorized"})
                return False

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/healthz":
                    self._json(200, {"status": "ok"})
                    return
                if parsed.path == "/platform/v1/agents/launch-events":
                    if not self._require_authorized():
                        return
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
                if not self._require_authorized():
                    return
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    self._json(415, {"error": "unsupported_media_type"})
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codestra Mission Control agent telemetry API")
    parser.add_argument("--db", default=".runtime/mission-control.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)

    store = MissionStore(Path(args.db).resolve())
    store.initialize()
    bearer_token = os.getenv("MISSION_CONTROL_AGENT_TELEMETRY_TOKEN")
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not bearer_token:
        raise SystemExit(
            "MISSION_CONTROL_AGENT_TELEMETRY_TOKEN is required for non-loopback binding"
        )
    server = AgentTelemetryAPI(store, bearer_token=bearer_token).server(
        host=args.host,
        port=args.port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
