"""Read-only desktop/server inventory and drift scanner.

Every subject (repository, worktree, host, runtime lease/execution) is observed
independently. A failure is recorded on that subject with an exact error code and
message and never aborts the rest of the inventory. Failed subjects carry the last
verified observation (non-authoritative) instead of a guessed value.

The scanner never fetches, pulls, writes the index or mutates any remote.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git_executor import GitExecutorError, GitWorktreeExecutor
from .network_fabric import normalize_status
from .store import MissionStore

StatusProvider = Callable[[], dict[str, Any]]

OK = "OK"
DRIFT = "DRIFT"
STALE = "STALE"
ABSENT = "ABSENT"
ERROR = "ERROR"
STATUSES = (OK, DRIFT, STALE, ABSENT, ERROR)
KINDS = ("repository", "worktree", "host", "runtime")

STALE_FLAGS = frozenset(
    {
        "never_fetched",
        "remote_refs_stale",
        "host_offline",
        "host_last_seen_stale",
        "host_missing_from_tailnet",
        "lease_expired",
        "heartbeat_stale",
        "execution_stale",
    }
)
INFO_FLAGS = frozenset({"no_upstream", "worktree_locked", "no_remote"})


class ProbeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RepositoryTarget:
    name: str
    path: str | None
    default_branch: str | None = None


@dataclass(frozen=True)
class ExpectedHost:
    hostname: str
    dns_name: str | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventoryThresholds:
    fetch_stale_seconds: int = 24 * 3600
    host_stale_seconds: int = 15 * 60
    heartbeat_stale_seconds: int = 5 * 60
    execution_stale_seconds: int = 15 * 60
    report_stale_seconds: int = 15 * 60


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    # Tailscale reports the zero time for peers that are currently connected.
    if parsed.year <= 1:
        return None
    return parsed


def _age(now: datetime, then: datetime | None) -> int | None:
    if then is None:
        return None
    return max(int((now - then).total_seconds()), 0)


def status_for(flags: Iterable[str]) -> str:
    flags = set(flags)
    if flags & STALE_FLAGS:
        return STALE
    if flags - INFO_FLAGS:
        return DRIFT
    return OK


def _item(
    kind: str,
    key: str,
    observed_at: str,
    *,
    repository: str | None = None,
    status: str | None = None,
    flags: Iterable[str] = (),
    details: dict[str, Any] | None = None,
    error: ProbeError | None = None,
) -> dict[str, Any]:
    flag_list = sorted(set(flags))
    if error is not None:
        resolved = ERROR
    else:
        resolved = status or status_for(flag_list)
    return {
        "kind": kind,
        "key": key,
        "repository": repository,
        "status": resolved,
        "flags": flag_list,
        "details": details or {},
        "error": ({"code": error.code, "message": error.message} if error is not None else None),
        "observed_at": observed_at,
    }


class GitProbe:
    """Read-only git runner with timeouts and exact error reporting."""

    def __init__(self, git: str | None = None, *, timeout: float = 20.0) -> None:
        self._git = git
        self.timeout = timeout

    @property
    def git(self) -> str:
        if self._git is None:
            try:
                self._git = GitWorktreeExecutor._discover_git()
            except GitExecutorError as exc:
                raise ProbeError("git_unavailable", str(exc)) from exc
        return self._git

    def run(self, repo: Path, *args: str) -> str:
        env = dict(os.environ)
        env.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        command = " ".join(args)
        try:
            proc = subprocess.run(
                [self.git, "-c", f"safe.directory={repo}", "-C", str(repo), *args],
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProbeError(
                "git_timeout",
                f"git {command} timed out after {self.timeout:g}s in {repo}",
            ) from exc
        except OSError as exc:
            raise ProbeError("git_unavailable", f"{type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
            raise ProbeError(
                "git_command_failed",
                f"git {command} exited {proc.returncode} in {repo}: {detail}",
            )
        return proc.stdout


def parse_status_v2(output: str) -> dict[str, Any]:
    """Parse ``git status --porcelain=v2 --branch`` into drift counters."""

    state: dict[str, Any] = {
        "head_sha": None,
        "branch": None,
        "detached": False,
        "upstream": None,
        "upstream_gone": False,
        "ahead": None,
        "behind": None,
        "tracked_changes": 0,
        "untracked_count": 0,
        "conflicted_count": 0,
    }
    for line in output.splitlines():
        if line.startswith("# branch.oid "):
            oid = line[len("# branch.oid ") :].strip()
            state["head_sha"] = None if oid == "(initial)" else oid
        elif line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            state["detached"] = head == "(detached)"
            state["branch"] = None if state["detached"] else head
        elif line.startswith("# branch.upstream "):
            state["upstream"] = line[len("# branch.upstream ") :].strip()
        elif line.startswith("# branch.ab "):
            ahead, behind = line[len("# branch.ab ") :].split()
            state["ahead"] = int(ahead.lstrip("+"))
            state["behind"] = int(behind.lstrip("-"))
        elif line.startswith(("1 ", "2 ")):
            state["tracked_changes"] += 1
        elif line.startswith("u "):
            state["conflicted_count"] += 1
        elif line.startswith("? "):
            state["untracked_count"] += 1
    # An upstream is configured but git cannot compute ahead/behind: the remote
    # tracking ref no longer exists (deleted remotely or never fetched).
    state["upstream_gone"] = state["upstream"] is not None and state["ahead"] is None
    return state


def parse_worktree_list(output: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return entries


class InventoryScanner:
    def __init__(
        self,
        *,
        store: MissionStore | None = None,
        probe: GitProbe | None = None,
        thresholds: InventoryThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.probe = probe or GitProbe()
        self.thresholds = thresholds or InventoryThresholds()
        self.clock = clock or (lambda: datetime.now(UTC))

    # Repositories and worktrees ------------------------------------------------

    def scan_repository(self, target: RepositoryTarget) -> list[dict[str, Any]]:
        now = self.clock()
        observed = _iso(now)
        name = target.name
        if not target.path:
            return [
                _item(
                    "repository",
                    name,
                    observed,
                    repository=name,
                    status=ABSENT,
                    flags=("not_cloned",),
                    details={"path": None},
                )
            ]
        path = Path(target.path)
        details: dict[str, Any] = {"path": str(path), "default_branch": target.default_branch}
        if not path.exists():
            return [
                _item(
                    "repository",
                    name,
                    observed,
                    repository=name,
                    details=details,
                    error=ProbeError("repository_path_missing", f"path does not exist: {path}"),
                )
            ]
        try:
            common = self.probe.run(path, "rev-parse", "--git-common-dir").strip()
        except ProbeError as exc:
            code = "not_a_git_repository" if exc.code == "git_command_failed" else exc.code
            return [
                _item(
                    "repository",
                    name,
                    observed,
                    repository=name,
                    details=details,
                    error=ProbeError(code, exc.message),
                )
            ]

        flags: list[str] = []
        error: ProbeError | None = None
        common_dir = Path(common)
        if not common_dir.is_absolute():
            common_dir = (path / common_dir).resolve()
        try:
            remotes = self.probe.run(path, "remote").split()
            details["remotes"] = remotes
            if not remotes:
                flags.append("no_remote")
            else:
                fetch_head = common_dir / "FETCH_HEAD"
                fetched = (
                    datetime.fromtimestamp(fetch_head.stat().st_mtime, UTC)
                    if fetch_head.is_file()
                    else None
                )
                details["last_fetch_at"] = _iso(fetched) if fetched else None
                details["fetch_age_seconds"] = _age(now, fetched)
                if fetched is None:
                    flags.append("never_fetched")
                elif details["fetch_age_seconds"] > self.thresholds.fetch_stale_seconds:
                    flags.append("remote_refs_stale")
        except ProbeError as exc:
            error = exc

        items: list[dict[str, Any]] = []
        try:
            entries = parse_worktree_list(self.probe.run(path, "worktree", "list", "--porcelain"))
        except ProbeError as exc:
            entries = []
            error = error or exc
        details["worktree_count"] = len(entries)

        for entry in entries:
            items.append(self._scan_worktree_safely(name, entry))

        drifted = sum(1 for item in items if item["status"] != OK)
        details["worktrees_not_ok"] = drifted
        items.insert(
            0,
            _item(
                "repository",
                name,
                observed,
                repository=name,
                flags=flags,
                details=details,
                error=error,
            ),
        )
        return items

    def _scan_worktree_safely(self, repository: str, entry: dict[str, str]) -> dict[str, Any]:
        raw_path = entry.get("worktree", "")
        key = f"{repository}:{raw_path}"
        try:
            return self.scan_worktree(repository, entry)
        except ProbeError as exc:
            return _item(
                "worktree",
                key,
                _iso(self.clock()),
                repository=repository,
                details={"path": raw_path},
                error=exc,
            )
        except Exception as exc:  # noqa: BLE001 - one worktree must not collapse the scan
            return _item(
                "worktree",
                key,
                _iso(self.clock()),
                repository=repository,
                details={"path": raw_path},
                error=ProbeError("unexpected_error", f"{type(exc).__name__}: {exc}"),
            )

    def scan_worktree(self, repository: str, entry: dict[str, str]) -> dict[str, Any]:
        observed = _iso(self.clock())
        raw_path = entry.get("worktree", "")
        key = f"{repository}:{raw_path}"
        path = Path(raw_path)
        details: dict[str, Any] = {"path": raw_path}
        flags: list[str] = []
        if "locked" in entry:
            flags.append("worktree_locked")
            details["locked_reason"] = entry["locked"] or None
        if "bare" in entry:
            details["bare"] = True
            return _item(
                "worktree", key, observed, repository=repository, flags=flags, details=details
            )
        if not path.exists():
            reason = entry.get("prunable") or "registered worktree path is absent"
            return _item(
                "worktree",
                key,
                observed,
                repository=repository,
                details=details,
                error=ProbeError(
                    "worktree_path_missing", f"worktree path missing: {path} ({reason})"
                ),
            )

        state = parse_status_v2(self.probe.run(path, "status", "--porcelain=v2", "--branch"))
        local_only = 0
        if state["head_sha"]:
            local_only = int(
                self.probe.run(path, "rev-list", "--count", "HEAD", "--not", "--remotes").strip()
                or 0
            )
        ahead = state["ahead"] or 0
        behind = state["behind"] or 0
        details.update(state)
        details["dirty_count"] = (
            state["tracked_changes"] + state["untracked_count"] + state["conflicted_count"]
        )
        details["local_only_commits"] = local_only
        details["pending_push_commits"] = max(ahead, local_only)

        if state["head_sha"] is None:
            flags.append("no_commits")
        if state["tracked_changes"]:
            flags.append("dirty")
        if state["untracked_count"]:
            flags.append("untracked")
        if state["conflicted_count"]:
            flags.append("conflicted")
        if state["detached"]:
            flags.append("detached_head")
        elif state["upstream"] is None:
            flags.append("no_upstream")
        if state["upstream_gone"]:
            flags.append("upstream_gone")
        if ahead:
            flags.append("ahead")
        if behind:
            flags.append("behind")
        if ahead and behind:
            flags.append("diverged")
        if local_only:
            flags.append("local_only")
        if details["pending_push_commits"]:
            flags.append("pending_push")
        return _item("worktree", key, observed, repository=repository, flags=flags, details=details)

    # Hosts ----------------------------------------------------------------------

    def scan_hosts(
        self,
        expected: Iterable[ExpectedHost],
        status_provider: StatusProvider | None,
    ) -> list[dict[str, Any]]:
        expected = list(expected)
        now = self.clock()
        observed = _iso(now)
        if not expected and status_provider is None:
            return []

        def failed(error: ProbeError) -> list[dict[str, Any]]:
            return [
                _item(
                    "host",
                    host.hostname,
                    observed,
                    details={"dns_name": host.dns_name, "roles": list(host.roles)},
                    error=error,
                )
                for host in expected
            ] or [_item("host", "tailnet", observed, error=error)]

        if status_provider is None:
            return failed(
                ProbeError("host_observation_unavailable", "no tailnet status source configured")
            )
        try:
            payload = status_provider()
        except Exception as exc:  # noqa: BLE001 - host source failure is data, not a crash
            return failed(
                ProbeError("host_observation_unavailable", f"{type(exc).__name__}: {exc}")
            )
        if not isinstance(payload, dict):
            return failed(
                ProbeError(
                    "host_observation_invalid",
                    f"tailnet status must be a JSON object, got {type(payload).__name__}",
                )
            )
        try:
            nodes = normalize_status(payload)
        except Exception as exc:  # noqa: BLE001
            return failed(ProbeError("host_observation_invalid", f"{type(exc).__name__}: {exc}"))

        def norm(value: str | None) -> str:
            return (value or "").strip().rstrip(".").lower()

        by_dns = {norm(node.dns_name): node for node in nodes if node.dns_name}
        by_name = {norm(node.hostname): node for node in nodes}
        matched: set[int] = set()
        items: list[dict[str, Any]] = []
        for host in expected:
            node = by_dns.get(norm(host.dns_name)) if host.dns_name else None
            node = node or by_name.get(norm(host.hostname))
            details: dict[str, Any] = {"dns_name": host.dns_name, "roles": list(host.roles)}
            if node is None:
                items.append(
                    _item(
                        "host",
                        host.hostname,
                        observed,
                        flags=("host_missing_from_tailnet",),
                        details=details,
                    )
                )
                continue
            matched.add(id(node))
            items.append(self._host_item(host.hostname, node, now, details))
        for node in nodes:
            if id(node) in matched:
                continue
            item = self._host_item(node.hostname, node, now, {"dns_name": node.dns_name})
            item["flags"] = sorted({*item["flags"], "host_unexpected"})
            item["status"] = status_for(item["flags"])
            items.append(item)
        return items

    def _host_item(
        self,
        key: str,
        node: Any,
        now: datetime,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        last_seen = _parse_time(node.last_seen)
        details = {
            **details,
            "observed_hostname": node.hostname,
            "ipv4": node.ipv4,
            "online": node.online,
            "last_seen": _iso(last_seen) if last_seen else None,
            "last_seen_age_seconds": _age(now, last_seen),
        }
        flags: list[str] = []
        if not node.online:
            flags.append("host_offline")
            age = details["last_seen_age_seconds"]
            if age is None or age > self.thresholds.host_stale_seconds:
                flags.append("host_last_seen_stale")
        return _item("host", key, _iso(now), flags=flags, details=details)

    # Runtime --------------------------------------------------------------------

    def scan_runtime(self) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        now = self.clock()
        observed = _iso(now)
        items: list[dict[str, Any]] = []
        try:
            for lease in self.store.list_leases():
                heartbeat = _parse_time(lease["heartbeat_at"])
                expires = _parse_time(lease["expires_at"])
                details = {
                    "mission_id": lease["mission_id"],
                    "agent_id": lease["agent_id"],
                    "role": lease["role"],
                    "heartbeat_at": lease["heartbeat_at"],
                    "expires_at": lease["expires_at"],
                    "heartbeat_age_seconds": _age(now, heartbeat),
                }
                flags: list[str] = []
                if expires is None or expires <= now:
                    flags.append("lease_expired")
                elif (
                    details["heartbeat_age_seconds"] is None
                    or details["heartbeat_age_seconds"] > self.thresholds.heartbeat_stale_seconds
                ):
                    flags.append("heartbeat_stale")
                items.append(
                    _item(
                        "runtime",
                        f"lease:{lease['mission_id']}",
                        observed,
                        flags=flags,
                        details=details,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            items.append(
                _item(
                    "runtime",
                    "leases",
                    observed,
                    error=ProbeError("runtime_observation_failed", f"{type(exc).__name__}: {exc}"),
                )
            )
        try:
            for execution in self.store.list_agent_executions(states=("RUNNING",)):
                updated = _parse_time(execution["updated_at"])
                details = {
                    "mission_id": execution["mission_id"],
                    "agent_id": execution["agent_id"],
                    "provider": execution["provider"],
                    "state": execution["state"],
                    "runner_pid": execution["runner_pid"],
                    "updated_at": execution["updated_at"],
                    "update_age_seconds": _age(now, updated),
                }
                flags = []
                if (
                    details["update_age_seconds"] is None
                    or details["update_age_seconds"] > self.thresholds.execution_stale_seconds
                ):
                    flags.append("execution_stale")
                items.append(
                    _item(
                        "runtime",
                        f"execution:{execution['execution_id']}",
                        observed,
                        flags=flags,
                        details=details,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            items.append(
                _item(
                    "runtime",
                    "executions",
                    observed,
                    error=ProbeError("runtime_observation_failed", f"{type(exc).__name__}: {exc}"),
                )
            )
        return items

    # Full scan ------------------------------------------------------------------

    def scan(
        self,
        *,
        repositories: Iterable[RepositoryTarget] = (),
        expected_hosts: Iterable[ExpectedHost] = (),
        status_provider: StatusProvider | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        scanned_at = _iso(self.clock())
        items: list[dict[str, Any]] = []
        for target in repositories:
            try:
                items.extend(self.scan_repository(target))
            except Exception as exc:  # noqa: BLE001 - one repo must not collapse the scan
                items.append(
                    _item(
                        "repository",
                        target.name,
                        _iso(self.clock()),
                        repository=target.name,
                        details={"path": target.path},
                        error=ProbeError("unexpected_error", f"{type(exc).__name__}: {exc}"),
                    )
                )
        items.extend(self.scan_hosts(expected_hosts, status_provider))
        items.extend(self.scan_runtime())

        report = {
            "scan_id": f"inv-{uuid.uuid4().hex}",
            "scanned_at": scanned_at,
            "items": items,
        }
        report.update(summarize(items))
        if self.store is not None:
            attach_last_verified(self.store, items, self.clock())
            if persist:
                self.store.record_inventory_scan(
                    report["scan_id"],
                    scanned_at=scanned_at,
                    complete=report["complete"],
                    summary=report["summary"],
                    observations=items,
                )
        return report


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = {status: 0 for status in STATUSES}
    by_kind: dict[str, dict[str, int]] = {}
    errors = []
    for item in items:
        by_status[item["status"]] += 1
        kind = by_kind.setdefault(item["kind"], {status: 0 for status in STATUSES})
        kind[item["status"]] += 1
        if item["error"]:
            errors.append(
                {
                    "kind": item["kind"],
                    "key": item["key"],
                    "code": item["error"]["code"],
                    "message": item["error"]["message"],
                }
            )
    return {
        "complete": not errors,
        "summary": {
            "total": len(items),
            "by_status": by_status,
            "by_kind": by_kind,
            "errors": errors,
        },
    }


def attach_last_verified(
    store: MissionStore,
    items: list[dict[str, Any]],
    now: datetime,
) -> None:
    """Attach the last successful observation to failed items (never as a guess)."""

    for item in items:
        if item["status"] != ERROR:
            continue
        row = store.last_verified_inventory_observation(item["kind"], item["key"])
        if row is None:
            item["last_verified"] = None
            continue
        item["last_verified"] = {
            "authoritative": False,
            "scan_id": row["scan_id"],
            "observed_at": row["observed_at"],
            "age_seconds": _age(now, _parse_time(row["observed_at"])),
            "status": row["status"],
            "flags": json.loads(row["flags_json"]),
            "details": json.loads(row["details_json"]),
        }


def _row_item(row: Any) -> dict[str, Any]:
    return {
        "kind": row["kind"],
        "key": row["subject_key"],
        "repository": row["repository"],
        "status": row["status"],
        "flags": json.loads(row["flags_json"]),
        "details": json.loads(row["details_json"]),
        "error": (
            {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"]
            else None
        ),
        "observed_at": row["observed_at"],
    }


def latest_report(
    store: MissionStore,
    *,
    thresholds: InventoryThresholds | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    thresholds = thresholds or InventoryThresholds()
    now = now or datetime.now(UTC)
    scan = store.latest_inventory_scan()
    if scan is None:
        return None
    items = [_row_item(row) for row in store.inventory_observations(scan["scan_id"])]
    attach_last_verified(store, items, now)
    age = _age(now, _parse_time(scan["scanned_at"]))
    report = {
        "scan_id": scan["scan_id"],
        "scanned_at": scan["scanned_at"],
        "age_seconds": age,
        "report_stale": age is None or age > thresholds.report_stale_seconds,
        "items": items,
    }
    report.update(summarize(items))
    return report


def filter_report(
    report: dict[str, Any],
    *,
    kinds: Iterable[str] = (),
    statuses: Iterable[str] = (),
    repositories: Iterable[str] = (),
) -> dict[str, Any]:
    kinds, statuses, repositories = set(kinds), set(statuses), set(repositories)
    items = [
        item
        for item in report["items"]
        if (not kinds or item["kind"] in kinds)
        and (not statuses or item["status"] in statuses)
        and (not repositories or item["repository"] in repositories)
    ]
    filtered = {key: value for key, value in report.items() if key not in {"items", "summary"}}
    filtered["items"] = items
    filtered.update(summarize(items))
    # Completeness describes the whole scan, not the filtered view.
    filtered["complete"] = report["complete"]
    return filtered


# Source loaders ------------------------------------------------------------------


def registry_targets(store: MissionStore) -> list[RepositoryTarget]:
    # A registered-local repository whose path vanished must surface as an error,
    # while a never-cloned repository is ABSENT unless it has since appeared on disk.
    targets = []
    for row in store.list_repositories():
        local_path = row["local_path"]
        present = bool(row["local_present"]) or bool(local_path and Path(local_path).exists())
        targets.append(
            RepositoryTarget(
                name=row["repository"],
                path=local_path if present else None,
                default_branch=row["default_branch"],
            )
        )
    return targets


def load_expected_hosts(path: str | Path) -> list[ExpectedHost]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        ExpectedHost(
            hostname=str(node["hostname"]),
            dns_name=node.get("dns"),
            roles=tuple(node.get("roles") or ()),
        )
        for node in payload.get("nodes") or []
    ]


def tailscale_status_file(path: str | Path) -> StatusProvider:
    def provider() -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    return provider


def tailscale_status_live(executable: str = "tailscale", timeout: float = 15.0) -> StatusProvider:
    def provider() -> dict[str, Any]:
        proc = subprocess.run(
            [executable, "status", "--json"],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"tailscale status --json exited {proc.returncode}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return json.loads(proc.stdout)

    return provider
