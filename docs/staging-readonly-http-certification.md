# Codestra Mission Control — read-only staging HTTP certification

## Safety boundary

This is a **staging HTTP acceptance probe**, not a staging promotion or production
release certificate. It sends **GET only**, keeps a short-lived Keycloak token
in memory, refuses redirects and non-HTTPS targets, and validates certificates.
It does not issue credentials, modify campaigns, make calls, send messages,
initialize/unseal OpenBao, or enable provider effects.

The permitted dashboard authority is
https://dashboard.staging.internal.codestra.agency and the staging issuer is
https://auth-staging.codestra.co/realms/codestra. No other origins are permitted.

## Preconditions

1. Private DNS resolves to the approved Caddy staging listener; an existing
   production/canary Caddy or Kong listener is **not** staging certification.
2. The staging Keycloak realm has a short-lived RS256 JWT with approved
   audience (mission-control-backend), authorized party (mission-control-ui),
   dashboard.read scope and a permitted realm role.
3. The read-only backend PostgreSQL source returns at least one repository and
   one task; an empty, failed or unconfigured database is not a passing test.
4. The Caddy private CA is trusted in the OS, or MC_STAGING_CA_FILE points to
   its reviewed certificate. Never disable TLS certificate verification.
5. Caddy, Kong and the backend expose the same nine authenticated GET routes
   and deny public metrics, internal and unknown endpoints.

## Run from an approved staging workstation

Enter a short-lived staging token at the terminal **without echo/history**:

    read -rs MC_STAGING_BEARER_TOKEN
    echo
    export MC_STAGING_BEARER_TOKEN
    # Optional only if the internal Caddy CA is not in system trust:
    # export MC_STAGING_CA_FILE=/approved/staging-ca.pem
    python3 scripts/probe_staging_auth_path.py
    unset MC_STAGING_BEARER_TOKEN

For offline source regression tests (no network or token):

    PYTHONPATH=src python3 -m pytest -q tests/test_staging_auth_path_probe.py tests/test_staging_gateway_parity.py

The probe verifies OIDC issuer/JWKS discovery, anonymous and malformed-token
denial, all nine database-backed read paths, Cache-Control: no-store,
private-surface 404 denial and unknown-route 404 denial. No POST, PATCH,
DELETE, calling, messaging or activation requests are sent.

## Failure and certification meanings

- PRIVATE_DNS_UNRESOLVED: fix private staging DNS rather than creating a
  public bypass record.
- TLS_OR_NETWORK_UNAVAILABLE: fix the private CA or staging listener; do not
  suppress certificate errors.
- STATUS_MISMATCH: inspect Caddy/Kong/auth and the specific read endpoint.
- TASKS_MISSING or HTTP 503: PostgreSQL/read-model staging is not ready.
- http_probe=PASS: **read-only staging HTTP** passed, not production.

The emitted runtime_exact_sha_certified=false, staging_promotion_go=NO and
production_go=NO remain until independent supply chain, exact-SHA, DB recovery,
observability, rollback, provider denial and CODEOWNER certification pass.
