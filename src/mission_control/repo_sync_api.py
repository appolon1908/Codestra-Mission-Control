from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .repo_sync import SELF_HOSTED_REMOTE, RepoSyncError, RepoSyncInspector
from .store import MissionStore

PREFIX = "/platform/v1/repo-sync"


class RepoSyncAPI:
    """Read-only HTTP surface for repo-sync and publication-auth readiness.

    Repositories are addressed by registry name only; callers can never point the
    inspector at an arbitrary filesystem path. Only GET is served.
    """

    def __init__(self, store: MissionStore, inspector: RepoSyncInspector) -> None:
        self.store = store
        self.inspector = inspector

    def resolve(self, name: str) -> tuple[int, dict | Path]:
        row = self.store.get_repository(name)
        if not row:
            return 404, {"error": "repository_not_registered", "repository": name}
        local_path = row["local_path"]
        if not local_path or not row["local_present"] or not Path(local_path).exists():
            return 409, {
                "error": "repository_not_present_locally",
                "repository": name,
                "local_path": local_path,
            }
        return 200, Path(local_path)

    def repository_report(self, name: str, *, live: bool) -> tuple[int, dict]:
        code, target = self.resolve(name)
        if code != 200:
            return code, target  # type: ignore[return-value]
        try:
            report = self.inspector.report(target, live=live)  # type: ignore[arg-type]
        except RepoSyncError as exc:
            return 422, {"error": "repo_sync_failed", "repository": name, "detail": str(exc)}
        payload = report.to_dict()
        payload["repository"] = name
        return 200, payload

    def readiness(self, name: str, remote: str, *, live: bool) -> tuple[int, dict]:
        code, target = self.resolve(name)
        if code != 200:
            return code, target  # type: ignore[return-value]
        try:
            status = self.inspector.status(target, live=live)  # type: ignore[arg-type]
        except RepoSyncError as exc:
            return 422, {"error": "repo_sync_failed", "repository": name, "detail": str(exc)}
        sync = status.remote(remote)
        auth = self.inspector.github_auth() if sync and sync.kind == "GITHUB" else None
        decision = self.inspector.publish_decision(status, remote, auth)
        payload = asdict(decision)
        payload["repository"] = name
        return 200, payload

    def repositories(self) -> dict:
        items = []
        for row in self.store.list_repositories():
            name = row["repository"]
            code, payload = self.repository_report(name, live=False)
            if code != 200:
                items.append({"repository": name, "error": payload.get("error")})
                continue
            status = payload["status"]
            items.append(
                {
                    "repository": name,
                    "branch": status["branch"],
                    "head_sha": status["head_sha"],
                    "dirty": status["dirty"],
                    "local_only": status["local_only"],
                    "pending_push": status["pending_push"],
                    "self_hosted_publication": payload["self_hosted_publication"],
                    "github_publication": payload["github_publication"],
                }
            )
        return {
            "count": len(items),
            "dirty": sum(1 for item in items if item.get("dirty")),
            "pending_push": sum(1 for item in items if item.get("pending_push")),
            "errors": sum(1 for item in items if "error" in item),
            "items": items,
        }

    def dispatch(self, raw_path: str) -> tuple[int, dict]:
        parsed = urlparse(raw_path)
        query = parse_qs(parsed.query)
        live = query.get("live", ["0"])[0] in {"1", "true"}
        path = parsed.path.rstrip("/")
        if path == "/health":
            return 200, {"ok": True, "service": "mission-control-repo-sync-api"}
        if path == f"{PREFIX}/auth":
            auth = self.inspector.github_auth()
            return 200, {
                "github": asdict(auth),
                "self_hosted_remote": SELF_HOSTED_REMOTE,
                "self_hosted_requires_github_auth": False,
            }
        if path == f"{PREFIX}/repositories":
            return 200, self.repositories()
        base = f"{PREFIX}/repositories/"
        if path.startswith(base):
            parts = path[len(base) :].split("/")
            name = unquote(parts[0])
            if not name or name in {".", ".."}:
                return 404, {"error": "not_found"}
            if len(parts) == 1:
                return self.repository_report(name, live=live)
            if len(parts) == 2 and parts[1] == "readiness":
                remote = query.get("remote", [SELF_HOSTED_REMOTE])[0]
                return self.readiness(name, remote, live=live)
        return 404, {"error": "not_found"}

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/repo-sync-v1"

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

            def do_GET(self) -> None:
                self._json(*api.dispatch(self.path))

            def _read_only(self) -> None:
                self._json(405, {"error": "method_not_allowed", "allowed": ["GET"]})

            do_POST = do_PUT = do_PATCH = do_DELETE = _read_only

        return ThreadingHTTPServer((host, port), Handler)
