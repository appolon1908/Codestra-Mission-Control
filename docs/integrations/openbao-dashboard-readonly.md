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


## Optional live health observation (separate from release certification)

An operator may invoke GET /platform/v1/dashboard/openbao/health. This route
requires mission:read and its server-side probe uses a fixed, administrator-set
OPENBAO_HEALTH_URL (for example, https://bao.internal.example:8200/v1/sys/health)
and a matching OPENBAO_HEALTH_ALLOWED_HOSTS DNS allowlist. The browser may not
supply a URL, hostname or OpenBao token. The handler rejects HTTP, redirects,
credential-bearing URLs, query strings, unexpected ports and unrelated routes.
The TLS certificate is verified, GET is limited to 3 seconds, and responses
are capped at 4096 bytes. Only initialized, sealed, standby and HTTP status
are returned (no version, cluster identity or secret data).

NOT_CONFIGURED, CONFIG_REJECTED, INVALID_RESPONSE or UNAVAILABLE explicitly
mean that runtime health was not observed. An OBSERVED sealed/uninitialized
vault is not ready, and even an unsealed observation is NOT a staging or
production authorization. This request is read-only and must not start
or initialize OpenBao. The test suite uses a fake HTTP opener; it never
contacts a live secret service.
