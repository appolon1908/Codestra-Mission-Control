from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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
    last_seen: str | None = None


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
                last_seen=raw.get("LastSeen"),
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
