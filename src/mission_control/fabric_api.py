from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .network_fabric import (
    DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    DEFAULT_OFFLINE_STALE_AFTER_SECONDS,
    FabricConfigError,
    FabricHealthReport,
    FabricInventory,
    FabricState,
    PolicyValidation,
    StatusSnapshot,
    evaluate_health,
    validate_bind_host,
    validate_policy,
)

PREFIX = "/platform/v1/fabric"


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    return value


def _counts(report: FabricHealthReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in report.nodes:
        counts[node.state.value] = counts.get(node.state.value, 0) + 1
    counts["unexpected"] = len(report.unexpected_nodes)
    return counts


def health_payload(report: FabricHealthReport) -> dict[str, Any]:
    payload = to_jsonable(report)
    payload["counts"] = _counts(report)
    return payload


def policy_payload(result: PolicyValidation) -> dict[str, Any]:
    return to_jsonable(result)


class FabricRuntime:
    """Read-only fabric runtime: inventory + tailscale status readback + policy validation."""

    def __init__(
        self,
        inventory: FabricInventory,
        policy: dict[str, Any],
        status_reader: Callable[[], StatusSnapshot],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_snapshot_age_seconds: int = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
        offline_stale_after_seconds: int = DEFAULT_OFFLINE_STALE_AFTER_SECONDS,
        cache_seconds: float = 5.0,
    ) -> None:
        self.inventory = inventory
        self.policy = policy
        self.status_reader = status_reader
        self.clock = clock
        self.max_snapshot_age_seconds = max_snapshot_age_seconds
        self.offline_stale_after_seconds = offline_stale_after_seconds
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached: tuple[datetime, StatusSnapshot] | None = None

    def snapshot(self) -> StatusSnapshot:
        with self._lock:
            now = self.clock()
            if self._cached and (now - self._cached[0]).total_seconds() < self.cache_seconds:
                return self._cached[1]
            snapshot = self.status_reader()
            self._cached = (now, snapshot)
            return snapshot

    def health(self) -> FabricHealthReport:
        return evaluate_health(
            self.inventory,
            self.snapshot(),
            now=self.clock(),
            max_snapshot_age_seconds=self.max_snapshot_age_seconds,
            offline_stale_after_seconds=self.offline_stale_after_seconds,
        )

    def policy_validation(self) -> PolicyValidation:
        return validate_policy(self.policy)

    def runtime(self, *, bind_host: str, bind_port: int) -> dict[str, Any]:
        report = self.health()
        policy = self.policy_validation()
        bind_finding = validate_bind_host(bind_host, tailnet=self.inventory.tailnet)
        return {
            "service": "mission-control-fabric-api",
            "tailnet": self.inventory.tailnet,
            "bind": {
                "host": bind_host,
                "port": bind_port,
                "private": bind_finding is None,
                "finding": to_jsonable(bind_finding),
            },
            "public_endpoint_required": False,
            "read_only": True,
            "thresholds": {
                "max_snapshot_age_seconds": self.max_snapshot_age_seconds,
                "offline_stale_after_seconds": self.offline_stale_after_seconds,
            },
            "health": {
                "state": report.state.value,
                "checked_at": report.checked_at.isoformat(),
                "snapshot_source": report.snapshot_source,
                "snapshot_age_seconds": report.snapshot_age_seconds,
                "counts": _counts(report),
                "errors": [
                    to_jsonable(item) for item in report.findings if item.severity == "error"
                ],
            },
            "policy": {
                "valid": policy.valid,
                "grants_checked": policy.grants_checked,
                "error_codes": sorted(
                    {item.code for item in policy.findings if item.severity == "error"}
                ),
            },
        }


class FabricAPI:
    def __init__(self, runtime: FabricRuntime) -> None:
        self.runtime = runtime

    def server(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        finding = validate_bind_host(host, tailnet=self.runtime.inventory.tailnet)
        if finding is not None:
            raise FabricConfigError(f"{finding.code}: {finding.message}")
        runtime = self.runtime

        class Handler(BaseHTTPRequestHandler):
            server_version = "CodestraMissionControl/fabric-v1"

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

            def _read_only(self) -> None:
                self._json(405, {"error": "method_not_allowed", "read_only": True})

            do_POST = do_PUT = do_PATCH = do_DELETE = _read_only

            def do_GET(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path == "/health":
                    self._json(200, {"ok": True, "service": "mission-control-fabric-api"})
                    return
                if path == f"{PREFIX}/health":
                    report = runtime.health()
                    status = 200 if report.state is FabricState.HEALTHY else 503
                    self._json(status, health_payload(report))
                    return
                if path == f"{PREFIX}/nodes":
                    report = runtime.health()
                    status = 503 if report.state is FabricState.UNAVAILABLE else 200
                    self._json(
                        status,
                        {
                            "state": report.state.value,
                            "checked_at": report.checked_at.isoformat(),
                            "count": len(report.nodes),
                            "nodes": to_jsonable(report.nodes),
                            "unexpected_nodes": to_jsonable(report.unexpected_nodes),
                            "findings": to_jsonable(
                                [item for item in report.findings if item.node is None]
                            ),
                        },
                    )
                    return
                node_prefix = f"{PREFIX}/nodes/"
                if path.startswith(node_prefix):
                    hostname = unquote(path[len(node_prefix) :])
                    if not hostname or "/" in hostname:
                        self._json(404, {"error": "not_found"})
                        return
                    report = runtime.health()
                    node = report.node(hostname)
                    if node is None:
                        self._json(404, {"error": "node_not_in_inventory", "hostname": hostname})
                        return
                    status = 503 if report.state is FabricState.UNAVAILABLE else 200
                    payload = to_jsonable(node)
                    payload["fabric_state"] = report.state.value
                    payload["fabric_findings"] = to_jsonable(
                        [item for item in report.findings if item.node is None]
                    )
                    self._json(status, payload)
                    return
                if path == f"{PREFIX}/policy/validation":
                    result = runtime.policy_validation()
                    self._json(200 if result.valid else 422, policy_payload(result))
                    return
                if path == f"{PREFIX}/runtime":
                    host, port = self.server.server_address[:2]
                    self._json(200, runtime.runtime(bind_host=str(host), bind_port=int(port)))
                    return
                self._json(404, {"error": "not_found"})

        return ThreadingHTTPServer((host, port), Handler)
