# Tailscale Mission Control Fabric

## Verified nodes

| Node | Role | MagicDNS | Tailscale IPv4 |
|---|---|---|---|
| codestra-desktop-postman | workstation, agent-worker | codestra-desktop-postman.tail4a23a1.ts.net | 100.66.139.30 |
| codestra-laptop-appolon | workstation, agent-worker | codestra-laptop-appolon.tail4a23a1.ts.net | 100.64.209.77 |
| codestra-middleware-core | mission-control, middleware | codestra-middleware-core.tail4a23a1.ts.net | 100.76.208.87 |
| codestra-vicidial | telephony | codestra-vicidial.tail4a23a1.ts.net | 100.68.154.51 |
| codestra-breero-apps | apps | codestra-breero-apps.tail4a23a1.ts.net | 100.92.10.23 |
| codestra-klyrow-obs | apps, observability | codestra-klyrow-obs.tail4a23a1.ts.net | 100.87.244.15 |

Server 1 reports all six nodes online. Desktop and laptop have distinct MagicDNS names and Tailscale addresses.

## Control-plane boundary

Temporal gRPC/UI, Mission Control internals, database management, agent dispatch, and private monitoring use the tailnet. Public Caddy/Kong routes remain the product/API edge only.

The desired ACL/grant plan is stored in config/tailscale.policy-plan.json. Agent-worker identities do not receive production/provider-effect authority.
