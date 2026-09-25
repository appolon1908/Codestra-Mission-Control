from __future__ import annotations

import ipaddress
import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6_NETWORK = ipaddress.ip_network("fd7a:115c:a1e0::/48")
DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 300
DEFAULT_OFFLINE_STALE_AFTER_SECONDS = 86_400

_ZERO_TIME_PREFIX = "0001-01-01"
_FRACTION = re.compile(r"\.(\d+)")
_PORT_SPEC = re.compile(r"^(tcp|udp):(\d{1,5})$")
_WILDCARD_SELECTORS = frozenset(
    {"*", "autogroup:member", "autogroup:internet", "autogroup:danger-all", "autogroup:tagged"}
)
_BROAD_TAG_OWNERS = frozenset({"*", "autogroup:member", "autogroup:tagged"})


class NodeRole(StrEnum):
    MISSION_CONTROL = "mission-control"
    WORKSTATION = "workstation"
    AGENT_WORKER = "agent-worker"
    MIDDLEWARE = "middleware"
    TELEPHONY = "telephony"
    APPS = "apps"
    OBSERVABILITY = "observability"


# Roles that carry production/provider-effect capability. Agent workers must never reach them.
PRODUCTION_EFFECT_ROLES = frozenset({NodeRole.MIDDLEWARE, NodeRole.TELEPHONY, NodeRole.APPS})
# Workstations are user-owned devices and are not tagged; every other role maps to tag:<role>.
UNTAGGED_ROLES = frozenset({NodeRole.WORKSTATION})


class NodeState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    STALE = "stale"
    MISSING = "missing"
    UNVERIFIED = "unverified"


class FabricState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class FabricConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TailnetNode:
    hostname: str
    dns_name: str | None
    ipv4: str | None
    online: bool
    roles: tuple[NodeRole, ...] = ()
    os: str | None = None
    tags: tuple[str, ...] = ()
    last_seen: datetime | None = None
    is_self: bool = False


@dataclass(frozen=True)
class FabricRule:
    sources: tuple[NodeRole, ...]
    destinations: tuple[NodeRole, ...]
    ports: tuple[int, ...]
    purpose: str


@dataclass(frozen=True)
class FabricFinding:
    code: str
    severity: str
    message: str
    node: str | None = None


@dataclass(frozen=True)
class InventoryNode:
    hostname: str
    dns_name: str | None
    ipv4: str | None
    os: str | None
    roles: tuple[NodeRole, ...]

    @property
    def expected_tags(self) -> tuple[str, ...]:
        return tuple(f"tag:{role}" for role in self.roles if role not in UNTAGGED_ROLES)


@dataclass(frozen=True)
class FabricInventory:
    tailnet: str | None
    nodes: tuple[InventoryNode, ...]


@dataclass(frozen=True)
class StatusSnapshot:
    source: str
    payload: dict[str, Any] | None = None
    captured_at: datetime | None = None
    error: FabricFinding | None = None


@dataclass(frozen=True)
class NodeHealth:
    hostname: str
    dns_name: str | None
    roles: tuple[NodeRole, ...]
    state: NodeState
    expected_ipv4: str | None
    observed_ipv4: str | None = None
    online: bool | None = None
    last_seen: datetime | None = None
    offline_seconds: int | None = None
    expected_tags: tuple[str, ...] = ()
    observed_tags: tuple[str, ...] = ()
    findings: tuple[FabricFinding, ...] = ()


@dataclass(frozen=True)
class FabricHealthReport:
    state: FabricState
    checked_at: datetime
    snapshot_source: str
    snapshot_captured_at: datetime | None
    snapshot_age_seconds: int | None
    nodes: tuple[NodeHealth, ...]
    unexpected_nodes: tuple[TailnetNode, ...]
    findings: tuple[FabricFinding, ...]

    def node(self, hostname: str) -> NodeHealth | None:
        key = hostname.lower()
        return next((item for item in self.nodes if item.hostname.lower() == key), None)


@dataclass(frozen=True)
class PolicyValidation:
    valid: bool
    findings: tuple[FabricFinding, ...]
    grants_checked: int
    required_flows: tuple[str, ...] = ()
    missing_flows: tuple[str, ...] = ()
    excess_flows: tuple[str, ...] = ()


def parse_tailscale_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith(_ZERO_TIME_PREFIX):
        return None
    # Tailscale emits nanosecond fractions; datetime only accepts up to microseconds.
    text = _FRACTION.sub(lambda match: "." + match.group(1)[:6].ljust(6, "0"), value, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _dns_key(value: str | None) -> str | None:
    return value.rstrip(".").lower() if value else None


def normalize_status(payload: dict[str, Any]) -> list[TailnetNode]:
    nodes: list[TailnetNode] = []
    self_node = payload.get("Self") or {}
    peers = list((payload.get("Peer") or {}).values())
    for index, raw in enumerate([self_node, *peers]):
        if not raw:
            continue
        is_self = index == 0
        ips = raw.get("TailscaleIPs") or raw.get("Addresses") or []
        ipv4 = next((ip for ip in ips if isinstance(ip, str) and ip.startswith("100.")), None)
        nodes.append(
            TailnetNode(
                hostname=str(raw.get("HostName") or raw.get("DNSName") or "unknown"),
                dns_name=raw.get("DNSName"),
                ipv4=ipv4,
                # A peer that does not report Online must not be assumed reachable.
                online=bool(raw.get("Online", is_self)),
                os=raw.get("OS"),
                tags=tuple(str(tag) for tag in raw.get("Tags") or ()),
                last_seen=parse_tailscale_time(raw.get("LastSeen")),
                is_self=is_self,
            )
        )
    dedup: dict[str, TailnetNode] = {}
    for node in nodes:
        key = _dns_key(node.dns_name) or node.hostname.lower()
        dedup[key] = node
    return sorted(dedup.values(), key=lambda item: (item.hostname.lower(), item.dns_name or ""))


def default_rules() -> tuple[FabricRule, ...]:
    return (
        FabricRule(
            (NodeRole.WORKSTATION, NodeRole.AGENT_WORKER),
            (NodeRole.MISSION_CONTROL,),
            (7233, 8233),
            "Temporal gRPC/UI over tailnet only",
        ),
        FabricRule(
            (NodeRole.MISSION_CONTROL,),
            (NodeRole.AGENT_WORKER, NodeRole.WORKSTATION),
            (22, 443),
            "agent dispatch and management",
        ),
        FabricRule(
            (NodeRole.MISSION_CONTROL, NodeRole.OBSERVABILITY),
            (NodeRole.MIDDLEWARE, NodeRole.TELEPHONY, NodeRole.APPS, NodeRole.OBSERVABILITY),
            (9100, 9115),
            "private health/metrics collection",
        ),
    )


def public_control_ports_forbidden() -> tuple[int, ...]:
    return (7233, 8233, 5432)


# --- inventory -----------------------------------------------------------------------------


def load_inventory(payload: dict[str, Any]) -> FabricInventory:
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise FabricConfigError("inventory_invalid: 'nodes' must be a non-empty list")
    nodes: list[InventoryNode] = []
    seen: set[str] = set()
    for raw in raw_nodes:
        hostname = str(raw.get("hostname") or "").strip()
        if not hostname:
            raise FabricConfigError("inventory_invalid: node without hostname")
        if hostname.lower() in seen:
            raise FabricConfigError(f"inventory_invalid: duplicate node {hostname}")
        seen.add(hostname.lower())
        try:
            roles = tuple(NodeRole(role) for role in raw.get("roles") or ())
        except ValueError as exc:
            raise FabricConfigError(f"inventory_invalid: {hostname}: {exc}") from exc
        if not roles:
            raise FabricConfigError(f"inventory_invalid: {hostname} has no roles")
        ipv4 = raw.get("ipv4")
        if ipv4 and ipaddress.ip_address(ipv4) not in TAILSCALE_IPV4_NETWORK:
            raise FabricConfigError(f"inventory_invalid: {hostname} ipv4 {ipv4} is not tailnet")
        nodes.append(
            InventoryNode(
                hostname=hostname,
                dns_name=raw.get("dns") or raw.get("dns_name"),
                ipv4=ipv4,
                os=raw.get("os"),
                roles=roles,
            )
        )
    return FabricInventory(tailnet=payload.get("tailnet"), nodes=tuple(nodes))


def load_inventory_file(path: str | Path) -> FabricInventory:
    return load_inventory(json.loads(Path(path).read_text(encoding="utf-8")))


# --- status snapshots ----------------------------------------------------------------------


def _snapshot_error(source: str, code: str, message: str) -> StatusSnapshot:
    return StatusSnapshot(source=source, error=FabricFinding(code, "error", message))


def _decode_status(source: str, text: str, captured_at: datetime) -> StatusSnapshot:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _snapshot_error(source, "tailscale_status_invalid_json", f"{source}: {exc}")
    if not isinstance(payload, dict):
        return _snapshot_error(
            source, "tailscale_status_invalid_json", f"{source}: top-level JSON is not an object"
        )
    return StatusSnapshot(source=source, payload=payload, captured_at=captured_at)


def read_status_command(
    *,
    binary: str = "tailscale",
    timeout_seconds: float = 10.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> StatusSnapshot:
    """Read-only `tailscale status --json`. Never mutates tailnet state."""
    source = f"command:{binary} status --json"
    try:
        completed = runner(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        return _snapshot_error(source, "tailscale_cli_missing", str(exc))
    except subprocess.TimeoutExpired:
        return _snapshot_error(
            source,
            "tailscale_status_timeout",
            f"{binary} status --json exceeded {timeout_seconds:g}s",
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return _snapshot_error(
            source,
            "tailscale_status_failed",
            f"exit {completed.returncode}: {detail}",
        )
    return _decode_status(source, completed.stdout, clock())


def read_status_file(path: str | Path) -> StatusSnapshot:
    target = Path(path)
    source = f"file:{target}"
    try:
        text = target.read_text(encoding="utf-8")
        captured_at = datetime.fromtimestamp(target.stat().st_mtime, UTC)
    except OSError as exc:
        return _snapshot_error(source, "status_snapshot_missing", str(exc))
    return _decode_status(source, text, captured_at)


# --- health --------------------------------------------------------------------------------


def _match(inventory_node: InventoryNode, observed: dict[str, TailnetNode]) -> TailnetNode | None:
    key = _dns_key(inventory_node.dns_name)
    if key and key in observed:
        return observed[key]
    hostname = inventory_node.hostname.lower()
    return next((node for node in observed.values() if node.hostname.lower() == hostname), None)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else "never"


def _node_health(
    expected: InventoryNode,
    node: TailnetNode | None,
    *,
    now: datetime,
    offline_stale_after_seconds: int,
) -> NodeHealth:
    base = {
        "hostname": expected.hostname,
        "dns_name": expected.dns_name,
        "roles": expected.roles,
        "expected_ipv4": expected.ipv4,
        "expected_tags": expected.expected_tags,
    }
    if node is None:
        finding = FabricFinding(
            "node_missing",
            "error",
            f"{expected.hostname} is in the inventory but absent from tailscale status",
            expected.hostname,
        )
        return NodeHealth(state=NodeState.MISSING, findings=(finding,), **base)

    findings: list[FabricFinding] = []
    offline_seconds: int | None = None
    if node.online:
        state = NodeState.ONLINE
    else:
        if node.last_seen:
            offline_seconds = max(int((now - node.last_seen).total_seconds()), 0)
        if offline_seconds is None or offline_seconds > offline_stale_after_seconds:
            state = NodeState.STALE
            findings.append(
                FabricFinding(
                    "node_stale",
                    "error",
                    f"{expected.hostname} offline since {_iso(node.last_seen)}"
                    f" (offline_seconds={offline_seconds},"
                    f" stale_after_seconds={offline_stale_after_seconds})",
                    expected.hostname,
                )
            )
        else:
            state = NodeState.OFFLINE
            findings.append(
                FabricFinding(
                    "node_offline",
                    "error",
                    f"{expected.hostname} offline since {_iso(node.last_seen)}"
                    f" (offline_seconds={offline_seconds})",
                    expected.hostname,
                )
            )
    if expected.ipv4 and node.ipv4 != expected.ipv4:
        findings.append(
            FabricFinding(
                "node_identity_mismatch",
                "error",
                f"{expected.hostname} expected ipv4 {expected.ipv4}, observed {node.ipv4}",
                expected.hostname,
            )
        )
    missing_tags = sorted(set(expected.expected_tags) - set(node.tags))
    if missing_tags:
        findings.append(
            FabricFinding(
                "node_tags_missing",
                "warning",
                f"{expected.hostname} lacks policy tags {', '.join(missing_tags)};"
                " least-privilege grants do not apply to it",
                expected.hostname,
            )
        )
    return NodeHealth(
        state=state,
        observed_ipv4=node.ipv4,
        online=node.online,
        last_seen=node.last_seen,
        offline_seconds=offline_seconds,
        observed_tags=node.tags,
        findings=tuple(findings),
        **base,
    )


def evaluate_health(
    inventory: FabricInventory,
    snapshot: StatusSnapshot,
    *,
    now: datetime | None = None,
    max_snapshot_age_seconds: int = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    offline_stale_after_seconds: int = DEFAULT_OFFLINE_STALE_AFTER_SECONDS,
) -> FabricHealthReport:
    checked_at = now or datetime.now(UTC)
    age = (
        max(int((checked_at - snapshot.captured_at).total_seconds()), 0)
        if snapshot.captured_at
        else None
    )

    def unverified(finding: FabricFinding, state: FabricState) -> FabricHealthReport:
        nodes = tuple(
            NodeHealth(
                hostname=item.hostname,
                dns_name=item.dns_name,
                roles=item.roles,
                state=NodeState.UNVERIFIED,
                expected_ipv4=item.ipv4,
                expected_tags=item.expected_tags,
            )
            for item in inventory.nodes
        )
        return FabricHealthReport(
            state, checked_at, snapshot.source, snapshot.captured_at, age, nodes, (), (finding,)
        )

    if snapshot.error or snapshot.payload is None:
        error = snapshot.error or FabricFinding(
            "tailscale_status_unavailable", "error", "no status payload"
        )
        return unverified(error, FabricState.UNAVAILABLE)
    backend = snapshot.payload.get("BackendState")
    if backend is not None and backend != "Running":
        return unverified(
            FabricFinding("tailscale_backend_not_running", "error", f"BackendState={backend}"),
            FabricState.UNAVAILABLE,
        )
    if age is not None and age > max_snapshot_age_seconds:
        return unverified(
            FabricFinding(
                "status_snapshot_stale",
                "error",
                f"status captured {snapshot.captured_at.isoformat()} is {age}s old"
                f" (max_snapshot_age_seconds={max_snapshot_age_seconds})",
            ),
            FabricState.STALE,
        )

    observed = {
        _dns_key(node.dns_name) or node.hostname.lower(): node
        for node in normalize_status(snapshot.payload)
    }
    matched: set[int] = set()
    nodes: list[NodeHealth] = []
    for expected in inventory.nodes:
        node = _match(expected, observed)
        if node is not None:
            matched.add(id(node))
        nodes.append(
            _node_health(
                expected,
                node,
                now=checked_at,
                offline_stale_after_seconds=offline_stale_after_seconds,
            )
        )
    unexpected = tuple(node for node in observed.values() if id(node) not in matched)
    findings = [finding for item in nodes for finding in item.findings]
    findings.extend(
        FabricFinding(
            "unexpected_node",
            "warning",
            f"{node.hostname} ({node.dns_name}, {node.ipv4}) is on the tailnet but not in the"
            " inventory",
            node.hostname,
        )
        for node in unexpected
    )
    state = (
        FabricState.DEGRADED
        if any(finding.severity == "error" for finding in findings)
        else FabricState.HEALTHY
    )
    return FabricHealthReport(
        state,
        checked_at,
        snapshot.source,
        snapshot.captured_at,
        age,
        tuple(nodes),
        unexpected,
        tuple(findings),
    )


# --- least-privilege policy validation -----------------------------------------------------


def role_tag(role: NodeRole) -> str:
    return f"tag:{role}"


def _role_for_tag(tag: str) -> NodeRole | None:
    if not tag.startswith("tag:"):
        return None
    try:
        return NodeRole(tag.removeprefix("tag:"))
    except ValueError:
        return None


def _flow(src: str, dst: str, port: str) -> str:
    return f"{src} -> {dst} {port}"


def required_flows(
    rules: Iterable[FabricRule], declared_tags: set[str]
) -> tuple[set[str], list[FabricFinding]]:
    flows: set[str] = set()
    findings: list[FabricFinding] = []
    for rule in rules:
        sources = sorted({role_tag(r) for r in rule.sources} & declared_tags)
        destinations = sorted({role_tag(r) for r in rule.destinations} & declared_tags)
        if not sources or not destinations:
            findings.append(
                FabricFinding(
                    "required_flow_unmappable",
                    "error",
                    f"rule '{rule.purpose}' has no declared source or destination tag",
                )
            )
            continue
        flows.update(
            _flow(src, dst, f"tcp:{port}")
            for src in sources
            for dst in destinations
            for port in rule.ports
        )
    return flows, findings


def _is_wildcard(selector: str) -> bool:
    if selector in _WILDCARD_SELECTORS:
        return True
    try:
        network = ipaddress.ip_network(selector, strict=False)
    except ValueError:
        return False
    return network.prefixlen == 0 or not (
        network.subnet_of(TAILSCALE_IPV4_NETWORK)
        if network.version == 4
        else network.subnet_of(TAILSCALE_IPV6_NETWORK)
    )


def validate_policy(
    policy: dict[str, Any],
    *,
    rules: Iterable[FabricRule] | None = None,
) -> PolicyValidation:
    findings: list[FabricFinding] = []

    def error(code: str, message: str) -> None:
        findings.append(FabricFinding(code, "error", message))

    tag_owners = policy.get("tagOwners", policy.get("tag_owners")) or {}
    declared_tags = set(tag_owners)
    for tag, owners in sorted(tag_owners.items()):
        if _role_for_tag(tag) is None:
            error("unknown_role_tag", f"{tag} does not map to a Mission Control role")
        broad = sorted(set(owners or ()) & _BROAD_TAG_OWNERS)
        if broad:
            error("tag_owner_too_broad", f"{tag} may be assigned by {', '.join(broad)}")

    if policy.get("acls"):
        error("legacy_acls_present", "legacy 'acls' bypass grant validation; use 'grants' only")

    for attr in policy.get("nodeAttrs") or ():
        if "funnel" in (attr.get("attr") or ()):
            error(
                "public_funnel_forbidden",
                f"funnel enabled for {', '.join(attr.get('target') or ())}; control plane must"
                " stay private",
            )

    never_public = {
        entry.get("port") for entry in policy.get("never_public") or () if isinstance(entry, dict)
    }
    missing_never_public = sorted(set(public_control_ports_forbidden()) - never_public)
    if missing_never_public:
        error(
            "never_public_incomplete",
            f"never_public omits control ports {', '.join(map(str, missing_never_public))}",
        )

    granted: set[str] = set()
    grants = policy.get("grants") or []
    for index, grant in enumerate(grants):
        label = f"grants[{index}]"
        sources = list(grant.get("src") or ())
        destinations = list(grant.get("dst") or ())
        ports = list(grant.get("ip") or ())
        if not sources or not destinations or not ports:
            if grant.get("app") and sources and destinations:
                continue
            error("grant_malformed", f"{label} requires non-empty src, dst and ip")
            continue
        selector_ok = True
        for selector in (*sources, *destinations):
            if _is_wildcard(selector):
                error("grant_wildcard_selector", f"{label} uses broad selector {selector}")
                selector_ok = False
            elif not selector.startswith("tag:"):
                error("grant_selector_not_role_tag", f"{label} selector {selector} is not a tag")
                selector_ok = False
            elif selector not in declared_tags:
                error("undeclared_tag", f"{label} references {selector} missing from tagOwners")
                selector_ok = False
        port_specs: list[str] = []
        for spec in ports:
            match = _PORT_SPEC.match(str(spec))
            if not match or not 1 <= int(match.group(2)) <= 65535:
                error("grant_port_too_broad", f"{label} port {spec} is not a single proto:port")
                continue
            port_specs.append(str(spec))
        source_roles = {_role_for_tag(src) for src in sources}
        destination_roles = {_role_for_tag(dst) for dst in destinations}
        if NodeRole.AGENT_WORKER in source_roles:
            blocked = sorted(str(role) for role in destination_roles & PRODUCTION_EFFECT_ROLES)
            if blocked:
                error(
                    "agent_worker_production_access",
                    f"{label} grants agent-worker access to production/provider-effect roles"
                    f" {', '.join(blocked)}",
                )
        if selector_ok:
            granted.update(
                _flow(src, dst, spec)
                for src in sources
                for dst in destinations
                for spec in port_specs
            )

    required, mapping_findings = required_flows(rules or default_rules(), declared_tags)
    findings.extend(mapping_findings)
    missing = sorted(required - granted)
    excess = sorted(granted - required)
    findings.extend(
        FabricFinding("required_flow_missing", "error", f"required flow not granted: {flow}")
        for flow in missing
    )
    findings.extend(
        FabricFinding(
            "grant_exceeds_required_flows", "error", f"flow not required by fabric rules: {flow}"
        )
        for flow in excess
    )
    return PolicyValidation(
        valid=not any(item.severity == "error" for item in findings),
        findings=tuple(findings),
        grants_checked=len(grants),
        required_flows=tuple(sorted(required)),
        missing_flows=tuple(missing),
        excess_flows=tuple(excess),
    )


def load_policy_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FabricConfigError(f"policy_invalid: {path} is not a JSON object")
    return payload


# --- private bind guard --------------------------------------------------------------------


def validate_bind_host(host: str, *, tailnet: str | None = None) -> FabricFinding | None:
    """Mission Control fabric endpoints bind to loopback or the tailnet only, never public."""
    if host == "localhost":
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        suffix = (tailnet or "ts.net").rstrip(".").lower()
        if host.rstrip(".").lower().endswith("." + suffix):
            return None
        return FabricFinding(
            "bind_host_not_private",
            "error",
            f"{host} is not loopback, a tailnet address, or a MagicDNS name under {suffix}",
        )
    if address.is_loopback:
        return None
    network = TAILSCALE_IPV4_NETWORK if address.version == 4 else TAILSCALE_IPV6_NETWORK
    if address in network:
        return None
    return FabricFinding(
        "public_bind_forbidden",
        "error",
        f"{host} would expose the control plane outside loopback/tailnet",
    )
