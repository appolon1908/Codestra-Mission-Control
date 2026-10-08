# OpenBao dashboard read-only integration

The Codestra platform UI uses a **Mission Control API read-model**, not direct
browser requests to OpenBao /v1 or a proxy for secret-engine operations.

- UI route: /mission-control/openbao
- API route: GET /platform/v1/dashboard/openbao
- Diagnostic routes: GET /platform/v1/dashboard/health and GET /platform/v1/dashboard/contract
- Permission: mission:read enforced server-side by Mission Control
- Response cache: no-store
- Authoritative data: repository registry, reconciled PR summary, Mission Router tasks
- Deliberately **not** represented: current vault seal/unseal state, secret values,
  certificate/private-key data, live application availability or release approval

The runtime_health and release_certification fields always report NOT_CHECKED.
The UI must not turn CI = GREEN or a passing HTTP check into production approval.
An empty repository registry returns NO_REGISTRY_DATA, not mock data or healthy data.

## Deployment routing

The browser uses same-origin paths. Caddy/Kong must explicitly route
/platform/v1/dashboard/* to the Mission Control backend (:8790 on the
private service network). Configure /mission-control/ws for the authenticated
WebSocket gateway and host the SPA /mission-control/openbao with history fallback.
Neither OpenBao :8200 nor internal metrics may be exposed publicly.
The Vite development proxy is **development only**, never public ingress.

Production must configure MISSION_CONTROL_AUTH_MODE=required, a valid Keycloak
issuer/audience and approved AZP values, alongside UI auth variables
VITE_MC_AUTH_REQUIRED=true, VITE_MC_OIDC_ISSUER, and VITE_MC_OIDC_CLIENT_ID.
Do not publish permissive local-development token mode or put a service account
or OpenBao token in the UI bundle.

## Certification sequence

1. Unit-test openbao_read_model.snapshot with missing and reconciled data.
2. Test HTTP 401 without bearer token and HTTP 200 with mission:read.
3. Build UI and run interactive tests (tabs, filter, refresh, links, contract check,
   no-data and API failure).
4. Verify same-origin reverse-proxy path with exact server URLs, TLS, CORS,
   and Keycloak token claims.
5. Verify live OpenBao runtime health independently server-side.
6. Keep STAGING_GO=NO and PRODUCTION_GO=NO until all release/security gates pass.

This module does not initialize/unseal OpenBao, read a secret, mutate roles,
change an image digest, or enable production effects.
