from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .inventory import KINDS, STATUSES, InventoryThresholds, filter_report, latest_report
from .store import MissionStore

MAX_BODY = 64 * 1024
DRIFT_PATH = "/platform/v1/inventory/drift"
SCANS_PATH = DRIFT_PATH + "/scans"
HOSTS_PATH = DRIFT_PATH + "/hosts"
REPOSITORIES_PREFIX = DRIFT_PATH + "/repositories/"

ScanRunner = Callable[[], dict[str, Any]]


class InventoryAPI:
    """Local drift inventory API.

    GET  /health
    GET  /platform/v1/inventory/drift[?kind=&status=&repository=]
    POST /platform/v1/inventory/drift/scans
    GET  /platform/v1/inventory/drift/repositories/{repository}
    GET  /platform/v1/inventory/drift/hosts
    """

    def __init__(
        self,
        store: MissionStore,
        run_scan: ScanRunner,
        *,
        thresholds: InventoryThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.run_scan = run_scan
        self.thresholds = thresholds or InventoryThresholds()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.scan_lock = threading.Lock()

    def latest(self) -> dict[str, Any] | None:
        return latest_report(self.store, thresholds=self.thresholds, now=self.clock())

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/inventory-v1"

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

            def _latest_or_404(self) -> dict[str, Any] | None:
                report = api.latest()
                if report is None:
                    self._json(404, {"error": "no_inventory_scan"})
                return report

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._json(200, {"ok": True, "service": "mission-control-inventory-api"})
                    return

                if parsed.path == DRIFT_PATH:
                    query = parse_qs(parsed.query)
                    kinds = query.get("kind", [])
                    statuses = [value.upper() for value in query.get("status", [])]
                    invalid = {
                        "kind": sorted(set(kinds) - set(KINDS)),
                        "status": sorted(set(statuses) - set(STATUSES)),
                    }
                    invalid = {key: value for key, value in invalid.items() if value}
                    if invalid:
                        self._json(400, {"error": "invalid_filter", "invalid": invalid})
                        return
                    report = self._latest_or_404()
                    if report is None:
                        return
                    self._json(
                        200,
                        filter_report(
                            report,
                            kinds=kinds,
                            statuses=statuses,
                            repositories=query.get("repository", []),
                        ),
                    )
                    return

                if parsed.path == HOSTS_PATH:
                    report = self._latest_or_404()
                    if report is None:
                        return
                    self._json(200, filter_report(report, kinds=("host", "runtime")))
                    return

                if parsed.path.startswith(REPOSITORIES_PREFIX):
                    repository = unquote(parsed.path[len(REPOSITORIES_PREFIX) :])
                    if not repository or "/" in repository:
                        self._json(404, {"error": "not_found"})
                        return
                    report = self._latest_or_404()
                    if report is None:
                        return
                    filtered = filter_report(report, repositories=(repository,))
                    if not filtered["items"]:
                        self._json(
                            404,
                            {
                                "error": "repository_not_in_inventory",
                                "repository": repository,
                                "scan_id": report["scan_id"],
                            },
                        )
                        return
                    filtered["repository"] = repository
                    self._json(200, filtered)
                    return

                self._json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != SCANS_PATH:
                    self._json(404, {"error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    self._json(400, {"error": "invalid_content_length"})
                    return
                if length < 0 or length > MAX_BODY:
                    self._json(400, {"error": "invalid_json_or_body_size"})
                    return
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._json(400, {"error": "invalid_json_or_body_size"})
                        return
                    if not isinstance(body, dict):
                        self._json(400, {"error": "invalid_json_or_body_size"})
                        return
                if not api.scan_lock.acquire(blocking=False):
                    self._json(409, {"error": "scan_in_progress"})
                    return
                try:
                    report = api.run_scan()
                except Exception as exc:  # noqa: BLE001 - surface exact scanner failure
                    self._json(
                        500,
                        {
                            "error": "scan_failed",
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    return
                finally:
                    api.scan_lock.release()
                self._json(201, report)

        return ThreadingHTTPServer((host, port), Handler)
