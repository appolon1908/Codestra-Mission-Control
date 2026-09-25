"""Loopback HTTP API exposing checkpoint dispatcher state.

Endpoints (JSON only, stdlib server, no provider effects):

- ``GET  /health``
- ``GET  /platform/v1/dispatcher/state``
- ``GET  /platform/v1/dispatcher/decisions?mission_id=&limit=``
- ``GET  /platform/v1/dispatcher/tasks?status=``
- ``GET  /platform/v1/dispatcher/proofs?mission_id=``
- ``POST /platform/v1/dispatcher/proofs``
- ``POST /platform/v1/dispatcher/run``
"""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .checkpoint_dispatch import CheckpointDispatcher, ImplementationProof

MAX_BODY = 1024 * 1024
MAX_LIMIT = 500
PREFIX = "/platform/v1/dispatcher"


class BadRequest(ValueError):
    pass


def _limit(query: dict[str, list[str]], default: int) -> int:
    raw = (query.get("limit") or [str(default)])[0]
    try:
        value = int(raw)
    except ValueError as exc:
        raise BadRequest("limit must be an integer") from exc
    if value < 1 or value > MAX_LIMIT:
        raise BadRequest(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def _str_list(body: dict, key: str) -> tuple[str, ...]:
    value = body.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BadRequest(f"{key} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _optional_str(body: dict, key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequest(f"{key} must be a string")
    return value.strip() or None


def proof_from_body(body: dict) -> ImplementationProof:
    missing = [
        key for key in ("mission_id", "agent_id") if not _optional_str(body, key)
    ]
    if missing:
        raise BadRequest(f"missing fields: {', '.join(missing)}")
    tests = body.get("tests") or {}
    if not isinstance(tests, dict):
        raise BadRequest("tests must be an object")
    pr_number = body.get("pr_number")
    if pr_number is not None and (isinstance(pr_number, bool) or not isinstance(pr_number, int)):
        raise BadRequest("pr_number must be an integer")
    return ImplementationProof(
        mission_id=str(_optional_str(body, "mission_id")),
        agent_id=str(_optional_str(body, "agent_id")),
        execution_id=_optional_str(body, "execution_id"),
        implementation_files=_str_list(body, "implementation_files"),
        api_endpoints=_str_list(body, "api_endpoints"),
        tests=tests,
        local_commit_sha=_optional_str(body, "local_commit_sha"),
        pushed_branch_sha=_optional_str(body, "pushed_branch_sha"),
        pr_number=pr_number,
        pr_url=_optional_str(body, "pr_url"),
        pr_head_sha=_optional_str(body, "pr_head_sha"),
        next_slice=_optional_str(body, "next_slice"),
    )


def decision_json(decision) -> dict:
    payload = asdict(decision)
    payload["classification"] = decision.classification.value
    payload["reasons"] = list(decision.reasons)
    return payload


class DispatchAPI:
    def __init__(self, dispatcher: CheckpointDispatcher) -> None:
        self.dispatcher = dispatcher

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        dispatcher = self.dispatcher

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/dispatcher-v1"

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

            def _body(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError as exc:
                    raise BadRequest("invalid Content-Length") from exc
                if length == 0:
                    return {}
                if length < 0 or length > MAX_BODY:
                    raise BadRequest("body size out of range")
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BadRequest("body must be valid JSON") from exc
                if not isinstance(body, dict):
                    raise BadRequest("body must be a JSON object")
                return body

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                try:
                    if parsed.path == "/health":
                        self._json(200, {"ok": True, "service": "mission-control-dispatcher"})
                    elif parsed.path == f"{PREFIX}/state":
                        self._json(200, dispatcher.state(recent=_limit(query, 20)))
                    elif parsed.path == f"{PREFIX}/decisions":
                        mission_id = (query.get("mission_id") or [None])[0]
                        items = dispatcher.decisions(
                            mission_id=mission_id, limit=_limit(query, 50)
                        )
                        self._json(200, {"count": len(items), "items": items})
                    elif parsed.path == f"{PREFIX}/tasks":
                        status = (query.get("status") or [None])[0]
                        items = dispatcher.tasks(status=status)
                        self._json(200, {"count": len(items), "items": items})
                    elif parsed.path == f"{PREFIX}/proofs":
                        mission_id = (query.get("mission_id") or [None])[0]
                        if not mission_id:
                            raise BadRequest("mission_id query parameter is required")
                        items = dispatcher.proofs(mission_id)
                        self._json(200, {"count": len(items), "items": items})
                    else:
                        self._json(404, {"error": "not_found"})
                except BadRequest as exc:
                    self._json(400, {"error": "bad_request", "detail": str(exc)})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                try:
                    body = self._body()
                    if parsed.path == f"{PREFIX}/proofs":
                        proof = proof_from_body(body)
                        try:
                            proof_id = dispatcher.record_proof(proof)
                        except KeyError:
                            self._json(
                                404,
                                {"error": "mission_not_found", "mission_id": proof.mission_id},
                            )
                            return
                        self._json(201, {"proof_id": proof_id, "mission_id": proof.mission_id})
                    elif parsed.path == f"{PREFIX}/run":
                        limit = body.get("limit", 100)
                        if isinstance(limit, bool) or not isinstance(limit, int):
                            raise BadRequest("limit must be an integer")
                        if limit < 1 or limit > MAX_LIMIT:
                            raise BadRequest(f"limit must be between 1 and {MAX_LIMIT}")
                        decisions = dispatcher.run(limit=limit)
                        self._json(
                            200,
                            {
                                "processed": len(decisions),
                                "decisions": [decision_json(item) for item in decisions],
                                "state": dispatcher.state(),
                            },
                        )
                    else:
                        self._json(404, {"error": "not_found"})
                except BadRequest as exc:
                    self._json(400, {"error": "bad_request", "detail": str(exc)})

        return ThreadingHTTPServer((host, port), Handler)
