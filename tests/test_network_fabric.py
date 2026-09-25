import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mission_control.network_fabric import (
    FabricState,
    NodeRole,
    NodeState,
    StatusSnapshot,
    default_rules,
    evaluate_health,
    load_inventory,
    load_inventory_file,
    load_policy_file,
    normalize_status,
    public_control_ports_forbidden,
    read_status_command,
    read_status_file,
    validate_bind_host,
    validate_policy,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 9, 25, 16, 0, tzinfo=UTC)


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


def test_peer_without_online_field_is_not_assumed_online():
    nodes = normalize_status(
        {
            "Self": {"HostName": "me", "DNSName": "me.t.ts.net.", "TailscaleIPs": ["100.1.1.1"]},
            "Peer": {"p": {"HostName": "peer", "DNSName": "peer.t.ts.net."}},
        }
    )
    by_host = {node.hostname: node for node in nodes}
    assert by_host["me"].online is True
    assert by_host["me"].is_self is True
    assert by_host["peer"].online is False


def test_last_seen_parses_nanoseconds_and_zero_time():
    nodes = normalize_status(
        {
            "Self": {"HostName": "me", "LastSeen": "0001-01-01T00:00:00Z", "Online": True},
            "Peer": {
                "p": {
                    "HostName": "peer",
                    "LastSeen": "2026-09-24T10:40:35.123456789Z",
                    "Tags": ["tag:apps"],
                }
            },
        }
    )
    by_host = {node.hostname: node for node in nodes}
    assert by_host["me"].last_seen is None
    assert by_host["peer"].last_seen == datetime(2026, 9, 24, 10, 40, 35, 123456, tzinfo=UTC)
    assert by_host["peer"].tags == ("tag:apps",)


def inventory():
    return load_inventory(
        {
            "tailnet": "t.ts.net",
            "nodes": [
                {
                    "hostname": "mc",
                    "dns": "mc.t.ts.net.",
                    "ipv4": "100.76.208.87",
                    "roles": ["mission-control"],
                },
                {
                    "hostname": "worker",
                    "dns": "worker.t.ts.net.",
                    "ipv4": "100.64.209.77",
                    "roles": ["workstation", "agent-worker"],
                },
                {
                    "hostname": "pbx",
                    "dns": "pbx.t.ts.net.",
                    "ipv4": "100.68.154.51",
                    "roles": ["telephony"],
                },
            ],
        }
    )


def peer(host, ip, *, online=True, last_seen=None, tags=()):
    return {
        "HostName": host,
        "DNSName": f"{host}.t.ts.net.",
        "TailscaleIPs": [ip, "fd7a:115c:a1e0::1"],
        "Online": online,
        "LastSeen": last_seen.isoformat().replace("+00:00", "Z") if last_seen else None,
        "Tags": list(tags),
    }


def snapshot(peers, *, self_node=None, captured_at=NOW, backend="Running"):
    return StatusSnapshot(
        source="test",
        payload={
            "BackendState": backend,
            "Self": self_node or peer("mc", "100.76.208.87", tags=["tag:mission-control"]),
            "Peer": {f"k{index}": item for index, item in enumerate(peers)},
        },
        captured_at=captured_at,
    )


def test_health_is_healthy_when_all_inventory_nodes_online_and_tagged():
    report = evaluate_health(
        inventory(),
        snapshot(
            [
                peer("worker", "100.64.209.77", tags=["tag:agent-worker"]),
                peer("pbx", "100.68.154.51", tags=["tag:telephony"]),
            ]
        ),
        now=NOW,
    )
    assert report.state is FabricState.HEALTHY
    assert {node.state for node in report.nodes} == {NodeState.ONLINE}
    assert report.findings == ()


def test_health_distinguishes_offline_stale_and_missing_with_exact_errors():
    report = evaluate_health(
        inventory(),
        snapshot(
            [
                peer(
                    "worker",
                    "100.64.209.77",
                    online=False,
                    last_seen=NOW - timedelta(minutes=10),
                    tags=["tag:agent-worker"],
                ),
            ],
            self_node=peer(
                "mc",
                "100.76.208.87",
                online=False,
                last_seen=NOW - timedelta(days=3),
                tags=["tag:mission-control"],
            ),
        ),
        now=NOW,
        offline_stale_after_seconds=3600,
    )
    assert report.state is FabricState.DEGRADED
    states = {node.hostname: node.state for node in report.nodes}
    assert states == {"mc": NodeState.STALE, "worker": NodeState.OFFLINE, "pbx": NodeState.MISSING}
    messages = {finding.code: finding.message for finding in report.findings}
    assert messages["node_offline"] == (
        "worker offline since 2026-09-25T15:50:00+00:00 (offline_seconds=600)"
    )
    assert messages["node_stale"] == (
        "mc offline since 2026-09-22T16:00:00+00:00"
        " (offline_seconds=259200, stale_after_seconds=3600)"
    )
    assert messages["node_missing"] == "pbx is in the inventory but absent from tailscale status"
    assert report.node("WORKER").offline_seconds == 600


def test_offline_node_never_seen_is_stale():
    report = evaluate_health(
        inventory(),
        snapshot(
            [
                peer("worker", "100.64.209.77", online=False, tags=["tag:agent-worker"]),
                peer("pbx", "100.68.154.51", tags=["tag:telephony"]),
            ]
        ),
        now=NOW,
    )
    worker = report.node("worker")
    assert worker.state is NodeState.STALE
    assert worker.findings[0].message.startswith("worker offline since never")


def test_identity_mismatch_tag_drift_and_unexpected_nodes():
    report = evaluate_health(
        inventory(),
        snapshot(
            [
                peer("worker", "100.99.0.1", tags=["tag:agent-worker"]),
                peer("pbx", "100.68.154.51"),
                peer("rogue", "100.100.1.1"),
            ]
        ),
        now=NOW,
    )
    assert report.state is FabricState.DEGRADED
    codes = [finding.code for finding in report.findings]
    assert "node_identity_mismatch" in codes
    tag_finding = next(item for item in report.findings if item.code == "node_tags_missing")
    assert tag_finding.severity == "warning"
    assert tag_finding.node == "pbx"
    assert [node.hostname for node in report.unexpected_nodes] == ["rogue"]


def test_warnings_alone_keep_fabric_healthy():
    report = evaluate_health(
        inventory(),
        snapshot(
            [
                peer("worker", "100.64.209.77", tags=["tag:agent-worker"]),
                peer("pbx", "100.68.154.51"),
            ]
        ),
        now=NOW,
    )
    assert report.state is FabricState.HEALTHY
    assert [finding.code for finding in report.findings] == ["node_tags_missing"]


def test_stale_snapshot_marks_every_node_unverified():
    report = evaluate_health(
        inventory(),
        snapshot([], captured_at=NOW - timedelta(minutes=10)),
        now=NOW,
        max_snapshot_age_seconds=300,
    )
    assert report.state is FabricState.STALE
    assert {node.state for node in report.nodes} == {NodeState.UNVERIFIED}
    assert report.findings[0].code == "status_snapshot_stale"
    assert report.findings[0].message == (
        "status captured 2026-09-25T15:50:00+00:00 is 600s old (max_snapshot_age_seconds=300)"
    )


def test_backend_not_running_is_unavailable():
    report = evaluate_health(inventory(), snapshot([], backend="Stopped"), now=NOW)
    assert report.state is FabricState.UNAVAILABLE
    assert report.findings[0].code == "tailscale_backend_not_running"
    assert report.findings[0].message == "BackendState=Stopped"


def test_status_command_errors_are_preserved_exactly():
    def missing(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "tailscale")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("tailscale", 10)

    def failed(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "failed to connect to local tailscaled\n")

    def garbage(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, "not json", "")

    assert read_status_command(runner=missing).error.code == "tailscale_cli_missing"
    assert read_status_command(runner=timeout).error.message == (
        "tailscale status --json exceeded 10s"
    )
    failure = read_status_command(runner=failed).error
    assert failure.code == "tailscale_status_failed"
    assert failure.message == "exit 1: failed to connect to local tailscaled"
    assert read_status_command(runner=garbage).error.code == "tailscale_status_invalid_json"

    report = evaluate_health(inventory(), read_status_command(runner=failed), now=NOW)
    assert report.state is FabricState.UNAVAILABLE
    assert report.findings == (failure,)


def test_status_command_is_read_only_invocation():
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"BackendState": "Running"}), "")

    result = read_status_command(runner=runner, clock=lambda: NOW)
    assert result.error is None
    assert result.captured_at == NOW
    assert calls[0][0] == ["tailscale", "status", "--json"]
    assert calls[0][1]["check"] is False


def test_status_file_missing_and_present(tmp_path):
    missing = read_status_file(tmp_path / "absent.json")
    assert missing.error.code == "status_snapshot_missing"
    target = tmp_path / "status.json"
    target.write_text(json.dumps({"BackendState": "Running"}), encoding="utf-8")
    present = read_status_file(target)
    assert present.error is None
    assert present.payload == {"BackendState": "Running"}
    assert present.captured_at is not None


def test_repository_inventory_and_policy_plan_are_consistent():
    inventory_file = load_inventory_file(CONFIG / "tailscale.nodes.json")
    assert inventory_file.tailnet == "tail4a23a1.ts.net"
    assert len({node.ipv4 for node in inventory_file.nodes}) == len(inventory_file.nodes)
    result = validate_policy(load_policy_file(CONFIG / "tailscale.policy-plan.json"))
    assert result.valid, result.findings
    assert result.missing_flows == ()
    assert result.excess_flows == ()


def base_policy():
    return json.loads((CONFIG / "tailscale.policy-plan.json").read_text(encoding="utf-8"))


def codes(policy):
    return {finding.code for finding in validate_policy(policy).findings}


def test_policy_rejects_agent_worker_production_access():
    policy = base_policy()
    policy["grants"].append(
        {"src": ["tag:agent-worker"], "dst": ["tag:telephony"], "ip": ["tcp:5038"]}
    )
    found = codes(policy)
    assert "agent_worker_production_access" in found
    assert "grant_exceeds_required_flows" in found


def test_policy_rejects_wildcards_broad_ports_and_undeclared_tags():
    policy = base_policy()
    policy["grants"].extend(
        [
            {"src": ["*"], "dst": ["tag:mission-control"], "ip": ["tcp:7233"]},
            {"src": ["tag:mission-control"], "dst": ["0.0.0.0/0"], "ip": ["tcp:443"]},
            {"src": ["tag:mission-control"], "dst": ["tag:agent-worker"], "ip": ["*"]},
            {"src": ["tag:ghost"], "dst": ["tag:apps"], "ip": ["tcp:80"]},
            {"src": ["group:ops"], "dst": ["tag:apps"], "ip": ["tcp:80"]},
            {"src": [], "dst": ["tag:apps"], "ip": ["tcp:80"]},
        ]
    )
    found = codes(policy)
    assert {
        "grant_wildcard_selector",
        "grant_port_too_broad",
        "undeclared_tag",
        "grant_selector_not_role_tag",
        "grant_malformed",
    } <= found


def test_policy_detects_missing_required_flows_and_tag_owner_issues():
    policy = base_policy()
    policy["grants"] = [grant for grant in policy["grants"] if "tcp:7233" not in grant["ip"]]
    policy["tag_owners"]["tag:mystery"] = ["autogroup:member"]
    result = validate_policy(policy)
    assert not result.valid
    assert "tag:agent-worker -> tag:mission-control tcp:7233" in result.missing_flows
    found = {finding.code for finding in result.findings}
    assert {"required_flow_missing", "unknown_role_tag", "tag_owner_too_broad"} <= found


def test_policy_rejects_funnel_legacy_acls_and_incomplete_never_public():
    policy = base_policy()
    policy["nodeAttrs"] = [{"target": ["tag:mission-control"], "attr": ["funnel"]}]
    policy["acls"] = [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]
    policy["never_public"] = [{"port": 7233}]
    found = codes(policy)
    assert {"public_funnel_forbidden", "legacy_acls_present", "never_public_incomplete"} <= found


def test_bind_host_must_be_loopback_or_tailnet():
    assert validate_bind_host("127.0.0.1") is None
    assert validate_bind_host("::1") is None
    assert validate_bind_host("localhost") is None
    assert validate_bind_host("100.76.208.87") is None
    assert validate_bind_host("fd7a:115c:a1e0::312f:d058") is None
    assert validate_bind_host("mc.tail4a23a1.ts.net", tailnet="tail4a23a1.ts.net") is None
    assert validate_bind_host("0.0.0.0").code == "public_bind_forbidden"
    assert validate_bind_host("203.0.113.10").code == "public_bind_forbidden"
    assert validate_bind_host("192.168.1.10").code == "public_bind_forbidden"
    assert validate_bind_host("api.example.com").code == "bind_host_not_private"
