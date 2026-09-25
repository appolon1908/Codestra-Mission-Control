from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address, ip_network
from typing import Any


class NodeRole(StrEnum):
    MISSION_CONTROL = "mission-control"
    WORKSTATION = "workstation"
    AGENT_WORKER = "agent-worker"
    MIDDLEWARE = "middleware"
    TELEPHONY = "telephony"
    APPS = "apps"
    OBSERVABILITY = "observability"


@dataclass(frozen=True)
class TailnetNode:
    hostname: str
    dns_name: str | None
    ipv4: str | None
    online: bool
    roles: tuple[NodeRole, ...] = ()


@dataclass(frozen=True)
class FabricRule:
    sources: tuple[NodeRole, ...]
    destinations: tuple[NodeRole, ...]
    ports: tuple[int, ...]
    purpose: str


def normalize_status(payload: dict[str, Any]) -> list[TailnetNode]:
    nodes: list[TailnetNode] = []
    self_node = payload.get("Self") or {}
    peers = list((payload.get("Peer") or {}).values())
    for raw in [self_node, *peers]:
        if not raw:
            continue
        ips = raw.get("TailscaleIPs") or raw.get("Addresses") or []
        ipv4 = next((ip for ip in ips if isinstance(ip, str) and ip.startswith("100.")), None)
        nodes.append(
            TailnetNode(
                hostname=str(raw.get("HostName") or raw.get("DNSName") or "unknown"),
                dns_name=raw.get("DNSName"),
                ipv4=ipv4,
                online=bool(raw.get("Online", True)),
            )
        )
    dedup: dict[str, TailnetNode] = {}
    for node in nodes:
        key = node.dns_name or node.hostname
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


_TAILSCALE_V4 = ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ip_network("fd7a:115c:a1e0::/48")


def is_control_plane_bind_safe(address: str) -> bool:
    normalized = address.strip().strip("[]")
    if normalized in {"localhost"}:
        return True
    if normalized in {"0.0.0.0", "::", "*", ""}:
        return False
    try:
        parsed = ip_address(normalized)
    except ValueError:
        return False
    return parsed.is_loopback or parsed in _TAILSCALE_V4 or parsed in _TAILSCALE_V6


def validate_fabric(
    nodes: list[TailnetNode],
    listeners: list[tuple[str, int]] | tuple[tuple[str, int], ...] = (),
) -> list[str]:
    violations: list[str] = []
    seen_ipv4: dict[str, str] = {}
    seen_dns: dict[str, str] = {}

    for node in nodes:
        if node.ipv4:
            prior = seen_ipv4.get(node.ipv4)
            if prior and prior != node.hostname:
                violations.append(
                    f"identity collision: {node.ipv4} used by {prior} and {node.hostname}"
                )
            seen_ipv4[node.ipv4] = node.hostname

        if node.dns_name:
            dns = node.dns_name.rstrip(".").lower()
            prior = seen_dns.get(dns)
            if prior and prior != node.hostname:
                violations.append(
                    f"identity collision: {dns} used by {prior} and {node.hostname}"
                )
            seen_dns[dns] = node.hostname

    forbidden = set(public_control_ports_forbidden())
    for address, port in listeners:
        if port in forbidden and not is_control_plane_bind_safe(address):
            violations.append(
                f"unsafe public control listener: {address}:{port}"
            )

    return sorted(set(violations))
