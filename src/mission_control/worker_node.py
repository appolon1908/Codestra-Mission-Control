from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .redaction import redact_text, secret_keys
from .store import MissionStore


class WorkerLane(StrEnum):
    BUILDER = "builder"
    REVIEWER = "reviewer"
    VERIFIER = "verifier"


class NodeReadiness(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


KNOWN_PROVIDERS = frozenset({"claude", "codex"})
HEARTBEAT_STALE_SECONDS = 300
AUTH_STALE_SECONDS = 3600
MAX_PARALLEL_WRITERS = 3


class NodeValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class NodeNotFound(KeyError):
    pass


@dataclass(frozen=True)
class NodeCapabilities:
    node_id: str
    hostname: str
    os: str
    lanes: tuple[WorkerLane, ...]
    providers: tuple[str, ...]
    worktree_root: str
    max_parallel_writers: int = MAX_PARALLEL_WRITERS
    tailnet_dns: str | None = None
    tools: dict[str, str] = field(default_factory=dict)
    worktree_root_writable: bool = False
    disk_free_bytes: int | None = None
    production_effects_enabled: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "os": self.os,
            "lanes": [lane.value for lane in self.lanes],
            "providers": list(self.providers),
            "worktree_root": self.worktree_root,
            "max_parallel_writers": self.max_parallel_writers,
            "tailnet_dns": self.tailnet_dns,
            "tools": dict(self.tools),
            "worktree_root_writable": self.worktree_root_writable,
            "disk_free_bytes": self.disk_free_bytes,
            "production_effects_enabled": self.production_effects_enabled,
        }


@dataclass(frozen=True)
class ProviderAuthStatus:
    provider: str
    authenticated: bool
    auth_method: str | None = None
    exit_code: int | None = None
    detail: str | None = None
    checked_at: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "authenticated": self.authenticated,
            "auth_method": self.auth_method,
            "exit_code": self.exit_code,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class ReadinessDecision:
    node_id: str
    state: NodeReadiness
    implementation_ready: bool
    lanes: tuple[str, ...]
    ready_providers: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    evaluated_at: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self.state.value,
            "implementation_ready": self.implementation_ready,
            "lanes": list(self.lanes),
            "ready_providers": list(self.ready_providers),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "evaluated_at": self.evaluated_at,
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _string_list(value: Any, name: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{name} must be a list of strings")
        return []
    return [item.strip() for item in value if item.strip()]


def parse_capabilities(node_id: str, body: Mapping[str, Any]) -> NodeCapabilities:
    """Validate a capability registration payload for a worker node."""
    errors: list[str] = []
    leaked = secret_keys(dict(body))
    if leaked:
        raise NodeValidationError(
            [f"secret-bearing fields are not accepted: {', '.join(sorted(leaked))}"]
        )
    node_id = node_id.strip()
    if not node_id or "/" in node_id:
        errors.append("node_id is required and must not contain '/'")
    body_node = body.get("node_id")
    if body_node is not None and str(body_node).strip() != node_id:
        errors.append("node_id in body does not match path")

    hostname = str(body.get("hostname") or "").strip()
    os_name = str(body.get("os") or "").strip().lower()
    worktree_root = str(body.get("worktree_root") or "").strip()
    for name, value in (
        ("hostname", hostname),
        ("os", os_name),
        ("worktree_root", worktree_root),
    ):
        if not value:
            errors.append(f"{name} is required")

    lanes: list[WorkerLane] = []
    for raw in _string_list(body.get("lanes"), "lanes", errors):
        try:
            lane = WorkerLane(raw.lower())
        except ValueError:
            errors.append(f"unknown lane: {raw}")
            continue
        if lane not in lanes:
            lanes.append(lane)
    if not lanes:
        errors.append("at least one lane is required")
    elif WorkerLane.BUILDER not in lanes:
        errors.append(
            "worker nodes must carry the builder lane; review-only or verify-only "
            "nodes are not registered"
        )

    providers: list[str] = []
    for raw in _string_list(body.get("providers"), "providers", errors):
        provider = raw.lower()
        if provider not in KNOWN_PROVIDERS:
            errors.append(f"unknown provider: {raw}")
        elif provider not in providers:
            providers.append(provider)
    if not providers:
        errors.append("at least one provider is required")

    max_parallel = body.get("max_parallel_writers", MAX_PARALLEL_WRITERS)
    if (
        isinstance(max_parallel, bool)
        or not isinstance(max_parallel, int)
        or not 1 <= max_parallel <= MAX_PARALLEL_WRITERS
    ):
        errors.append(f"max_parallel_writers must be an integer 1..{MAX_PARALLEL_WRITERS}")
        max_parallel = MAX_PARALLEL_WRITERS

    tools_raw = body.get("tools") or {}
    if not isinstance(tools_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in tools_raw.items()
    ):
        errors.append("tools must be an object of string versions")
        tools_raw = {}

    disk_free = body.get("disk_free_bytes")
    if disk_free is not None and (
        isinstance(disk_free, bool) or not isinstance(disk_free, int) or disk_free < 0
    ):
        errors.append("disk_free_bytes must be a non-negative integer")
        disk_free = None

    for flag in ("worktree_root_writable", "production_effects_enabled"):
        if flag in body and not isinstance(body[flag], bool):
            errors.append(f"{flag} must be a boolean")

    if errors:
        raise NodeValidationError(errors)

    tailnet_dns = body.get("tailnet_dns")
    return NodeCapabilities(
        node_id=node_id,
        hostname=hostname,
        os=os_name,
        lanes=tuple(lanes),
        providers=tuple(providers),
        worktree_root=worktree_root,
        max_parallel_writers=int(max_parallel),
        tailnet_dns=str(tailnet_dns).strip() if tailnet_dns else None,
        tools={key: redact_text(value, limit=200) for key, value in tools_raw.items()},
        worktree_root_writable=bool(body.get("worktree_root_writable", False)),
        disk_free_bytes=disk_free,
        production_effects_enabled=bool(body.get("production_effects_enabled", False)),
    )


def sanitize_auth_status(
    provider: str,
    raw: Mapping[str, Any],
    *,
    checked_at: str | None = None,
) -> ProviderAuthStatus:
    """Reduce an adapter auth probe to a secret-free, allowlisted status record."""
    provider = provider.strip().lower()
    if provider not in KNOWN_PROVIDERS:
        raise NodeValidationError([f"unknown provider: {provider}"])
    exit_code = raw.get("exit_code")
    auth_method = raw.get("auth_method")
    detail = raw.get("detail", raw.get("summary"))
    return ProviderAuthStatus(
        provider=provider,
        authenticated=raw.get("authenticated") is True,
        auth_method=redact_text(str(auth_method), limit=64) if auth_method else None,
        exit_code=exit_code if isinstance(exit_code, int) else None,
        detail=redact_text(str(detail), limit=300) if detail else None,
        checked_at=checked_at or _now().isoformat(),
    )


def evaluate_readiness(
    capabilities: NodeCapabilities,
    *,
    last_heartbeat_at: str | None,
    auth: Mapping[str, ProviderAuthStatus],
    now: datetime | None = None,
    heartbeat_stale_seconds: int = HEARTBEAT_STALE_SECONDS,
    auth_stale_seconds: int = AUTH_STALE_SECONDS,
) -> ReadinessDecision:
    """Decide whether a node can accept implementation (builder) work."""
    current = now or _now()
    reasons: list[str] = []
    warnings: list[str] = []

    if WorkerLane.BUILDER not in capabilities.lanes:
        reasons.append("builder lane is not registered")
    if capabilities.production_effects_enabled:
        reasons.append("production effects must be disabled on worker nodes")
    if "git" not in capabilities.tools:
        reasons.append("git is not available")
    if not capabilities.worktree_root_writable:
        reasons.append("worktree root is not writable")

    if not last_heartbeat_at:
        reasons.append("no heartbeat recorded")
    else:
        age = current - datetime.fromisoformat(last_heartbeat_at)
        if age > timedelta(seconds=heartbeat_stale_seconds):
            reasons.append(f"heartbeat stale ({int(age.total_seconds())}s)")

    ready_providers: list[str] = []
    for provider in capabilities.providers:
        status = auth.get(provider)
        if status is None or not status.checked_at:
            warnings.append(f"{provider}: auth status unknown")
            continue
        age = current - datetime.fromisoformat(status.checked_at)
        if age > timedelta(seconds=auth_stale_seconds):
            warnings.append(f"{provider}: auth status stale")
            continue
        if not status.authenticated:
            warnings.append(f"{provider}: not authenticated")
            continue
        ready_providers.append(provider)
    if not ready_providers:
        reasons.append("no authenticated implementation provider")

    if reasons:
        state = NodeReadiness.NOT_READY
    elif warnings:
        state = NodeReadiness.DEGRADED
    else:
        state = NodeReadiness.READY
    return ReadinessDecision(
        node_id=capabilities.node_id,
        state=state,
        implementation_ready=not reasons,
        lanes=tuple(lane.value for lane in capabilities.lanes),
        ready_providers=tuple(ready_providers),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        evaluated_at=current.isoformat(),
    )


def _tool_version(command: list[str]) -> str | None:
    if not shutil.which(command[0]):
        return None
    try:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return redact_text(proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "", limit=120)


def _writable(root: Path) -> bool:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".mc-probe-", delete=True):
            pass
        return True
    except OSError:
        return False


def probe_local_capabilities(
    node_id: str,
    *,
    lanes: list[str],
    providers: list[str],
    worktree_root: str | Path,
    tailnet_dns: str | None = None,
    max_parallel_writers: int = MAX_PARALLEL_WRITERS,
) -> dict[str, Any]:
    """Probe this machine and return a registration payload (no secrets)."""
    root = Path(worktree_root).expanduser()
    tools: dict[str, str] = {"python": sys.version.split()[0]}
    git = _tool_version(["git", "--version"])
    if git:
        tools["git"] = git
    writable = _writable(root)
    disk_free: int | None = None
    if writable:
        disk_free = shutil.disk_usage(root).free
    return {
        "node_id": node_id,
        "hostname": socket.gethostname(),
        "os": platform.system().lower() or os.name,
        "lanes": lanes,
        "providers": providers,
        "worktree_root": str(root),
        "max_parallel_writers": max_parallel_writers,
        "tailnet_dns": tailnet_dns,
        "tools": tools,
        "worktree_root_writable": writable,
        "disk_free_bytes": disk_free,
        "production_effects_enabled": False,
    }


class WorkerNodeRegistry:
    """Durable worker-node registry shared by the CLI and HTTP API."""

    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def register(self, node_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        capabilities = parse_capabilities(node_id, body)
        self.store.upsert_worker_node(capabilities.node_id, capabilities.as_payload())
        return self.snapshot(capabilities.node_id)

    def heartbeat(self, node_id: str) -> str:
        at = _now().isoformat()
        if not self.store.record_worker_heartbeat(node_id, at):
            raise NodeNotFound(node_id)
        return at

    def record_auth(
        self,
        node_id: str,
        provider: str,
        raw: Mapping[str, Any],
    ) -> ProviderAuthStatus:
        leaked = secret_keys(dict(raw))
        if leaked:
            raise NodeValidationError(
                [f"secret-bearing fields are not accepted: {', '.join(sorted(leaked))}"]
            )
        if "authenticated" in raw and not isinstance(raw["authenticated"], bool):
            raise NodeValidationError(["authenticated must be a boolean"])
        if not self.store.get_worker_node(node_id):
            raise NodeNotFound(node_id)
        status = sanitize_auth_status(provider, raw)
        self.store.record_provider_auth(node_id, status.as_payload())
        return status

    def probe_auth(
        self,
        node_id: str,
        probes: Mapping[str, Callable[[], Mapping[str, Any]]],
    ) -> list[ProviderAuthStatus]:
        results: list[ProviderAuthStatus] = []
        for provider, probe in probes.items():
            try:
                raw = dict(probe())
            except (FileNotFoundError, OSError) as exc:
                raw = {"authenticated": False, "detail": f"{type(exc).__name__}: {exc}"}
            raw = {key: value for key, value in raw.items() if not secret_keys({key: None})}
            results.append(self.record_auth(node_id, provider, raw))
        return results

    def _capabilities(self, node_id: str) -> tuple[NodeCapabilities, dict]:
        row = self.store.get_worker_node(node_id)
        if not row:
            raise NodeNotFound(node_id)
        payload = row["capabilities"]
        capabilities = NodeCapabilities(
            node_id=row["node_id"],
            hostname=payload["hostname"],
            os=payload["os"],
            lanes=tuple(WorkerLane(lane) for lane in payload["lanes"]),
            providers=tuple(payload["providers"]),
            worktree_root=payload["worktree_root"],
            max_parallel_writers=int(payload["max_parallel_writers"]),
            tailnet_dns=payload.get("tailnet_dns"),
            tools=dict(payload.get("tools") or {}),
            worktree_root_writable=bool(payload.get("worktree_root_writable")),
            disk_free_bytes=payload.get("disk_free_bytes"),
            production_effects_enabled=bool(payload.get("production_effects_enabled")),
        )
        return capabilities, row

    def auth(self, node_id: str) -> dict[str, ProviderAuthStatus]:
        return {
            item["provider"]: ProviderAuthStatus(**item)
            for item in self.store.list_provider_auth(node_id)
        }

    def readiness(self, node_id: str, *, now: datetime | None = None) -> ReadinessDecision:
        capabilities, row = self._capabilities(node_id)
        return evaluate_readiness(
            capabilities,
            last_heartbeat_at=row["last_heartbeat_at"],
            auth=self.auth(node_id),
            now=now,
        )

    def snapshot(self, node_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        capabilities, row = self._capabilities(node_id)
        decision = self.readiness(node_id, now=now)
        return {
            "node_id": node_id,
            "capabilities": capabilities.as_payload(),
            "registered_at": row["registered_at"],
            "updated_at": row["updated_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "provider_auth": [
                status.as_payload()
                for _, status in sorted(self.auth(node_id).items())
            ],
            "readiness": decision.as_payload(),
        }

    def list(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        return [
            self.snapshot(row["node_id"], now=now) for row in self.store.list_worker_nodes()
        ]
