# Mission Control authenticated read-only dashboard

Owner: Codestra-Mission-Control. This private service exclusively serves the
/platform/v1/dashboard namespace, not Middleware's canonical :8095 command API.
Ingress: Caddy -> Kong -> Mission Control internal-only service.

The backend validates Keycloak RS256 signature, issuer, audience, azp, expiry,
and Operator/Reviewer/Administrator role before any control-plane reads.
Anonymous access must be 401; tenant-level Viewer and Agent must be 403.
A missing PostgreSQL source must be 503, never a fake green dashboard.

Use a restricted PostgreSQL role with CONNECT and SELECT only. Connections
are configured read-only, 3s connect timeout and 5s statement timeout.
GitHub PR counts are NULL and CI UNVERIFIED until independently synchronized.
Agent live status requires a recent heartbeat and active lease. Worktree dirty
state is UNKNOWN_LOCAL_STATE without direct host verification.

Staging wiring requires: Keycloak audience mapping and PKCE client;
read-only PostgreSQL credentials from OpenBao; reviewed Caddy/Kong route with
JWT authorization, no public direct backend port, trust-header stripping,
rate limits, and private route denials; authenticated WSS or clearly OFFLINE
realtime transport; and UI/browser/Postman negative authorization testing.

Source-only check:
python -m pytest -q tests/test_authenticated_dashboard.py

Read-only Postman/Newman:
newman run postman/Codestra-Mission-Control-ReadOnly.postman_collection.json
  --env-var baseUrl=https://STAGING_GATEWAY --env-var accessToken=SHORT_LIVED_TOKEN

No live effects, provider activation, production changes, or code-owner bypass
are permitted. Staging certificate requires exact-SHA CI, independent reviews,
gateway integration evidence and rollback checks; production remains NO-GO.

Keycloak staging issuer authority: https://auth-staging.codestra.co/realms/codestra. Production uses a distinct issuer and must never reuse staging credentials.
