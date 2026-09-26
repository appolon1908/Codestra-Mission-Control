from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .mission_router import MissionRouter
from .router_store import RouterStore

PREFIX = "/platform/v1/mission-router"


class MissionRouterAPI:
    def __init__(self, store: RouterStore, *, router: MissionRouter | None = None) -> None:
        self.store = store
        self.router = router or MissionRouter()

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status: int, payload: object) -> None:
                body = json.dumps(payload, default=list).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == f"{PREFIX}/snapshot":
                    repo = query.get("repository", [None])[0]
                    if not repo:
                        return self._json(400, {"error": "repository_required"})
                    return self._json(200, api.store.snapshot(repo, router=api.router))
                if parsed.path == f"{PREFIX}/next":
                    repo = query.get("repository", [None])[0]
                    agent_id = query.get("agent_id", [None])[0]
                    if not repo or not agent_id:
                        return self._json(400, {"error": "repository_and_agent_id_required"})
                    agent = api.store.agent(agent_id)
                    if not agent:
                        return self._json(404, {"error": "agent_not_registered"})
                    ranked = api.router.rank(api.store.tasks(repo), agent)
                    return self._json(200, {"agent_id": agent_id, "repository": repo,
                        "next": [{"task_id": r.task.task_id, "score": r.score, "reasons": r.reasons}
                                 for r in ranked[:10]]})
                return self._json(404, {"error": "not_found"})

            def log_message(self, format: str, *args: object) -> None:
                return

        return ThreadingHTTPServer((host, port), Handler)
