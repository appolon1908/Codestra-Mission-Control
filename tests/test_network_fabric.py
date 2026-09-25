from mission_control.network_fabric import (
    NodeRole,
    TailnetNode,
    default_rules,
    is_control_plane_bind_safe,
    normalize_status,
    public_control_ports_forbidden,
    validate_fabric,
)


def test_normalize_tailscale_status():
    payload = {
        "Self": {
            "HostName": "middleware",
            "DNSName": "middleware.tailnet.ts.net.",
            "TailscaleIPs": ["100.76.208.87"],
            "Online": True,
        },
        "Peer": {
            "node1": {
                "HostName": "appolon",
                "DNSName": "appolon.tailnet.ts.net.",
                "TailscaleIPs": ["100.64.209.77"],
                "Online": True,
            }
        },
    }
    nodes = normalize_status(payload)
    assert len(nodes) == 2
    assert {node.ipv4 for node in nodes} == {"100.76.208.87", "100.64.209.77"}


def test_temporal_rules_are_private_role_scoped():
    rules = default_rules()
    temporal = next(rule for rule in rules if 7233 in rule.ports)
    assert NodeRole.MISSION_CONTROL in temporal.destinations
    assert NodeRole.WORKSTATION in temporal.sources


def test_sensitive_control_ports_are_never_public():
    ports = public_control_ports_forbidden()
    assert 7233 in ports
    assert 8233 in ports
    assert 5432 in ports


def test_control_plane_bind_safety():
    assert is_control_plane_bind_safe("127.0.0.1") is True
    assert is_control_plane_bind_safe("100.76.208.87") is True
    assert is_control_plane_bind_safe("fd7a:115c:a1e0::1") is True
    assert is_control_plane_bind_safe("0.0.0.0") is False
    assert is_control_plane_bind_safe("::") is False
    assert is_control_plane_bind_safe("203.0.113.10") is False
    assert is_control_plane_bind_safe("10.40.0.1") is False


def test_validate_fabric_rejects_public_control_listener():
    nodes = [
        TailnetNode(
            hostname="middleware",
            dns_name="middleware.tailnet.ts.net.",
            ipv4="100.76.208.87",
            online=True,
        )
    ]
    violations = validate_fabric(nodes, [("0.0.0.0", 7233), ("127.0.0.1", 5432)])
    assert violations == ["unsafe public control listener: 0.0.0.0:7233"]


def test_validate_fabric_detects_workstation_identity_collision():
    nodes = [
        TailnetNode(
            hostname="appolon",
            dns_name="worker.tailnet.ts.net.",
            ipv4="100.64.209.77",
            online=True,
            roles=(NodeRole.WORKSTATION, NodeRole.AGENT_WORKER),
        ),
        TailnetNode(
            hostname="desktop",
            dns_name="desktop.tailnet.ts.net.",
            ipv4="100.64.209.77",
            online=True,
            roles=(NodeRole.WORKSTATION, NodeRole.AGENT_WORKER),
        ),
    ]
    violations = validate_fabric(nodes)
    assert violations == [
        "identity collision: 100.64.209.77 used by appolon and desktop"
    ]
