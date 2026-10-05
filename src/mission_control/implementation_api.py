from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .store import MissionStore

MAX_BODY = 1024 * 1024


def _row_payload(row) -> dict:
    item = dict(row)
    for key in ("implementation_files_json", "api_endpoints_json", "tests_json"):
        if key in item:
            item[key.removesuffix("_json")] = json.loads(item.pop(key))
    if "api_required" in item:
        item["api_required"] = bool(item["api_required"])
    if "proof_matched" in item:
        item["proof_matched"] = bool(item["proof_matched"])
    return item


class ImplementationAPI:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def server(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> ThreadingHTTPServer:
        store = self.store

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/implementation-v2"

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
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None
                return body if isinstance(body, dict) else None

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._json(
                        200,
                        {
                            "ok": True,
                            "service": "mission-control-implementation-api",
                        },
                    )
                    return
                if parsed.path == "/platform/v1/agent-executions":
                    items = [_row_payload(row) for row in store.list_implementation_executions()]
                    self._json(200, {"count": len(items), "items": items})
                    return
                prefix = "/platform/v1/agent-executions/"
                if parsed.path.startswith(prefix):
                    execution_id = parsed.path[len(prefix) :]
                    if not execution_id or "/" in execution_id:
                        self._json(404, {"error": "not_found"})
                        return
                    row = store.get_implementation_execution(execution_id)
                    if not row:
                        self._json(
                            404,
                            {
                                "error": "execution_not_found",
                                "execution_id": execution_id,
                            },
                        )
                        return
                    self._json(200, _row_payload(row))
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                body = self._body()
                if body is None:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return

                if parsed.path == "/platform/v1/agent-executions":
                    required = {
                        "mission_id",
                        "agent_id",
                        "workstation",
                        "provider",
                        "branch",
                        "worktree",
                    }
                    missing = sorted(key for key in required if not str(body.get(key, "")).strip())
                    if missing:
                        self._json(
                            400,
                            {"error": "missing_fields", "fields": missing},
                        )
                        return
                    execution_id = str(body.get("execution_id") or f"impl-{uuid.uuid4().hex}")
                    try:
                        agent_number = store.start_implementation_execution(
                            execution_id=execution_id,
                            mission_id=str(body["mission_id"]),
                            agent_id=str(body["agent_id"]),
                            workstation=str(body["workstation"]),
                            provider=str(body["provider"]),
                            branch=str(body["branch"]),
                            worktree=str(body["worktree"]),
                            api_required=bool(body.get("api_required", False)),
                        )
                    except KeyError:
                        self._json(
                            404,
                            {
                                "error": "mission_not_found",
                                "mission_id": body["mission_id"],
                            },
                        )
                        return
                    except Exception as exc:  # pragma: no cover - DB uniqueness guard
                        self._json(
                            409,
                            {
                                "error": "execution_conflict",
                                "detail": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        return
                    self._json(
                        201,
                        {
                            "execution_id": execution_id,
                            "agent_number": agent_number,
                            "state": "STARTED",
                        },
                    )
                    return

                prefix = "/platform/v1/agent-executions/"
                suffix = "/proof"
                if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
                    execution_id = parsed.path[len(prefix) : -len(suffix)]
                    execution_id = execution_id.rstrip("/")
                    if not execution_id or "/" in execution_id:
                        self._json(404, {"error": "not_found"})
                        return
                    try:
                        decision = store.record_implementation_proof(
                            execution_id,
                            implementation_files=list(body.get("implementation_files") or []),
                            api_endpoints=list(body.get("api_endpoints") or []),
                            tests=dict(body.get("tests") or {}),
                            local_commit_sha=body.get("local_commit_sha"),
                            pushed_branch_sha=body.get("pushed_branch_sha"),
                            pr_number=body.get("pr_number"),
                            pr_url=body.get("pr_url"),
                            pr_head_sha=body.get("pr_head_sha"),
                        )
                    except KeyError:
                        self._json(
                            404,
                            {
                                "error": "execution_not_found",
                                "execution_id": execution_id,
                            },
                        )
                        return
                    payload = asdict(decision)
                    payload["state"] = decision.state.value
                    payload["reasons"] = list(decision.reasons)
                    self._json(200, payload)
                    return

                self._json(404, {"error": "not_found"})

        return ThreadingHTTPServer((host, port), Handler)
