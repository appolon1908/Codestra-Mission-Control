# Mission Control — Fail-Closed Runtime Authentication

## Authentication boundary

The executable dashboard/API runtime defaults to **Keycloak JWT validation required**. The prior silent disabled-token default is no longer acceptable for a deployable runtime. The runtime refuses to listen until an issuer is configured:

```bash
export MISSION_CONTROL_ENV=staging
export MISSION_CONTROL_AUTH_MODE=required
export MISSION_CONTROL_JWT_ISSUER=https://keycloak.example/realms/codestra
export MISSION_CONTROL_JWT_AUDIENCE=mission-control-backend
export MISSION_CONTROL_ALLOWED_AZP=mission-control-ui,websocket-gateway
python -m mission_control.runtime --host 127.0.0.1 --port 8790
```

Replace the example issuer with the real Keycloak realm before deployment. The issuer's JWKS must be reachable, its token signing key valid, and the required `iss`, `aud`, `azp`, expiry and roles must match the route permission. This change does **not** provision a Keycloak realm or claim that authentication has been tested against a live realm.

### Explicit isolated development only

For disposable test databases on loopback, with no external effect adapters or public ingress:

```bash
export MISSION_CONTROL_ENV=development
python -m mission_control.runtime --host 127.0.0.1 --port 8790 --local-dev-no-auth --db /tmp/mission-control-local-test.db
```

The `--local-dev-no-auth` flag refuses non-loopback hosts and refuses any environment other than `development` or `test`. Without that flag, `MISSION_CONTROL_AUTH_MODE=disabled` is rejected. The verifier independently rejects disabled authentication under `staging` or `production`. Unknown mode values fail.

**Production activation remains NO GO** until Caddy/Kong ingress, Keycloak authentication, roles, scopes, unauthorized rejection, tenant isolation, frontend authorized state and live runtime readback are independently certified. Do not expose the local developer server through an unauthenticated reverse proxy.
