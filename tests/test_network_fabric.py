from mission_control.network_fabric import (
    NodeRole,
    default_rules,
    normalize_status,
    public_control_ports_forbidden,
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
