from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .merge_coordinator import (
    EvidenceRejected,
    EvidenceRole,
    EvidenceVerdict,
    MergeCoordinator,
    PullRequestSnapshot,
    classify_conflicts,
)
from .store import MissionStore

MAX_BODY = 1024 * 1024
API_VERSION = "merge-coordinator-v1"
PREFIX = "/platform/v1/merge-coordinator"
MISSION_ROUTE = re.compile(
    rf"^{PREFIX}/missions/(?P<mission>[A-Za-z0-9._-]+)/"
    r"(?P<resource>head|evidence|dependencies|evaluations|decision|authorization)$"
)

ENDPOINTS = (
    "GET /health",
    f"POST {PREFIX}/conflicts/classify",
    f"POST {PREFIX}/missions/{{mission_id}}/head",
    f"POST {PREFIX}/missions/{{mission_id}}/evidence",
    f"POST {PREFIX}/missions/{{mission_id}}/dependencies",
    f"POST {PREFIX}/missions/{{mission_id}}/evaluations",
    f"GET {PREFIX}/missions/{{mission_id}}/decision",
    f"GET {PREFIX}/missions/{{mission_id}}/authorization",
)


class MergeCoordinatorAPI:
    def __init__(self, store: MissionStore) -> None:
        self.store = store
        self.coordinator = MergeCoordinator(store)

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        coordinator = self.coordinator

        class Handler(BaseHTTPRequestHandler):
            server_version = f"CodestraMissionControl/{API_VERSION}"

            def log_message(self, fmt: str, *args) -> None:
                return

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Mission-Control-API", API_VERSION)
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

            def _mission_not_found(self, mission_id: str) -> None:
                self._json(404, {"error": "mission_not_found", "mission_id": mission_id})

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/health":
                    self._json(
                        200,
                        {
                            "ok": True,
                            "service": "mission-control-merge-coordinator",
                            "endpoints": list(ENDPOINTS),
                        },
                    )
                    return
                match = MISSION_ROUTE.match(path)
                if not match or match["resource"] not in {"decision", "authorization"}:
                    self._json(404, {"error": "not_found"})
                    return
                mission_id = match["mission"]
                try:
                    if match["resource"] == "authorization":
                        self._json(200, coordinator.authorization(mission_id))
                        return
                    decision = coordinator.latest_decision(mission_id)
                except KeyError:
                    self._mission_not_found(mission_id)
                    return
                if decision is None:
                    self._json(404, {"error": "decision_not_found", "mission_id": mission_id})
                    return
                self._json(200, decision)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                body = self._body()
                if body is None:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return

                if path == f"{PREFIX}/conflicts/classify":
                    mergeable = body.get("mergeable")
                    paths = body.get("conflicted_paths") or []
                    if (mergeable is not None and not isinstance(mergeable, bool)) or not (
                        isinstance(paths, list)
                    ):
                        self._json(400, {"error": "invalid_classification_request"})
                        return
                    assessment = classify_conflicts(
                        mergeable,
                        [str(p) for p in paths],
                        cross_repo=bool(body.get("cross_repo", False)),
                    )
                    self._json(200, assessment.as_dict())
                    return

                match = MISSION_ROUTE.match(path)
                if not match or match["resource"] in {"decision", "authorization"}:
                    self._json(404, {"error": "not_found"})
                    return
                mission_id = match["mission"]
                resource = match["resource"]
                try:
                    if resource == "head":
                        result = coordinator.record_head(
                            mission_id, str(body.get("head_sha") or ""), str(body["actor"])
                        )
                        self._json(200, result)
                    elif resource == "evidence":
                        evidence_id = coordinator.record_evidence(
                            mission_id,
                            role=EvidenceRole(str(body["role"]).upper()),
                            head_sha=str(body["head_sha"]),
                            actor=str(body["actor"]),
                            verdict=EvidenceVerdict(str(body["verdict"]).upper()),
                            blockers=[str(b) for b in body.get("blockers") or []],
                        )
                        self._json(201, {"evidence_id": evidence_id, "mission_id": mission_id})
                    elif resource == "dependencies":
                        depends_on = str(body["depends_on"])
                        coordinator.store.add_dependency(mission_id, depends_on)
                        self._json(
                            201,
                            {
                                "mission_id": mission_id,
                                "dependencies": coordinator.store.dependencies(mission_id),
                            },
                        )
                    else:
                        snapshot = PullRequestSnapshot.from_dict(body)
                        decision = coordinator.evaluate(mission_id, snapshot)
                        self._json(200, decision.as_dict())
                except KeyError as exc:
                    missing = exc.args[0] if exc.args else ""
                    if missing == mission_id:
                        self._mission_not_found(mission_id)
                    else:
                        self._json(400, {"error": "missing_fields", "fields": [missing]})
                except EvidenceRejected as exc:
                    self._json(409, {"error": "evidence_rejected", "detail": str(exc)})
                except (TypeError, ValueError) as exc:
                    self._json(400, {"error": "invalid_request", "detail": str(exc)})

        return ThreadingHTTPServer((host, port), Handler)
