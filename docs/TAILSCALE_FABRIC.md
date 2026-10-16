# Tailscale Mission Control Fabric

## Verified nodes

| Node | Role | MagicDNS | Tailscale IPv4 |
|---|---|---|---|
| codestra-desktop-postman | workstation, agent-worker | codestra-desktop-postman.tail4a23a1.ts.net | 100.66.139.30 |
| codestra-desktop-ubuntu | workstation, agent-worker | codestra-desktop-ubuntu.tail4a23a1.ts.net | 100.74.74.86 |
| codestra-laptop-appolon | workstation, agent-worker | codestra-laptop-appolon.tail4a23a1.ts.net | 100.64.209.77 |
| codestra-middleware-core | mission-control, middleware | codestra-middleware-core.tail4a23a1.ts.net | 100.76.208.87 |
| codestra-vicidial | telephony | codestra-vicidial.tail4a23a1.ts.net | 100.68.154.51 |
| codestra-breero-apps | apps | codestra-breero-apps.tail4a23a1.ts.net | 100.92.10.23 |
| codestra-klyrow-obs | apps, observability | codestra-klyrow-obs.tail4a23a1.ts.net | 100.87.244.15 |

Server 1 reports all six nodes online. Desktop and laptop have distinct MagicDNS names and Tailscale addresses.

## Control-plane boundary

Temporal gRPC/UI, Mission Control internals, database management, agent dispatch, and private monitoring use the tailnet. Public Caddy/Kong routes remain the product/API edge only.

The desired ACL/grant plan is stored in config/tailscale.policy-plan.json. Agent-worker identities do not receive production/provider-effect authority.

## Runtime health and least-privilege validation

`mission_control.network_fabric` compares the inventory in config/tailscale.nodes.json with a
read-only `tailscale status --json` snapshot (or a captured status file). Nothing is ever written
to the tailnet.

Node states:

| State | Meaning | Finding code |
|---|---|---|
| online | reported online by tailscale | - |
| offline | offline for at most `--offline-stale-after` seconds (default 86400) | `node_offline` |
| stale | offline longer than that, or never seen | `node_stale` |
| missing | in the inventory but absent from the status | `node_missing` |
| unverified | the status is stale or unavailable, so no node claim can be made | fabric-level finding |

Fabric-level findings keep the exact error text: `tailscale_cli_missing`,
`tailscale_status_timeout`, `tailscale_status_failed` (exit code plus stderr),
`tailscale_status_invalid_json`, `status_snapshot_missing`, `tailscale_backend_not_running`,
and `status_snapshot_stale` (default max age 300s). Warnings (`node_tags_missing`,
`unexpected_node`) do not degrade health. `node_identity_mismatch` does.

`validate_policy` checks the grant plan against `default_rules()`. It requires every required
flow and rejects anything extra (`grant_exceeds_required_flows`). It also rejects wildcard or
non-tag selectors, undeclared tags, multi-port or wildcard port specs, broad tag owners, legacy
`acls`, Funnel, an incomplete `never_public` list, and any agent-worker grant to a
production/provider-effect role (middleware, telephony, apps).

## Read-only fabric API

```
PYTHONPATH=src python3 -m mission_control.cli serve-fabric-api --host 127.0.0.1 --port 8791
```

The server only binds to loopback, a Tailscale address (100.64.0.0/10, fd7a:115c:a1e0::/48), or a
MagicDNS name. Any other host fails with `public_bind_forbidden` or `bind_host_not_private`. No
public control-plane endpoint is required or allowed.

| Method | Path | Success | Failure |
|---|---|---|---|
| GET | /health | 200 liveness | - |
| GET | /platform/v1/fabric/health | 200 healthy | 503 degraded/stale/unavailable (full report body) |
| GET | /platform/v1/fabric/nodes | 200 node readback | 503 when status unavailable |
| GET | /platform/v1/fabric/nodes/{hostname} | 200 node | 404 `node_not_in_inventory`, 503 unavailable |
| GET | /platform/v1/fabric/policy/validation | 200 valid | 422 with findings |
| GET | /platform/v1/fabric/runtime | 200 bind, thresholds, health and policy summary | - |

Every other method returns 405 `method_not_allowed`. CLI equivalents: `fabric-health` and
`fabric-policy-validate`, which exit with 0 when healthy/valid and 2 otherwise.
