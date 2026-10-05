from __future__ import annotations
from dataclasses import dataclass
import os
import jwt

ROLE_PERMISSIONS = {
    "Administrator": {"*"},
    "Operator": {"mission:read", "mission:write", "agent:assign", "agent:stop", "router:lease"},
    "Reviewer": {
        "mission:read",
        "evidence:read",
        "evidence:certify",
        "review:approve",
        "pr:link",
        "agent:read",
    },
    "Viewer": {"mission:read", "evidence:read", "agent:read"},
    "Agent": {"agent:heartbeat", "agent:checkpoint", "evidence:submit", "task:claim"},
}


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401):
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    claims: dict

    def allows(self, permission: str) -> bool:
        return any(
            "*" in ROLE_PERMISSIONS.get(r, set()) or permission in ROLE_PERMISSIONS.get(r, set())
            for r in self.roles
        )


class KeycloakVerifier:
    def __init__(
        self,
        issuer: str | None = None,
        audience: str | None = None,
        azp: set[str] | None = None,
        mode: str | None = None,
    ):
        self.mode = (mode or os.getenv("MISSION_CONTROL_AUTH_MODE", "disabled")).lower()
        self.issuer = (issuer or os.getenv("MISSION_CONTROL_JWT_ISSUER", "")).rstrip("/")
        self.audience = audience or os.getenv(
            "MISSION_CONTROL_JWT_AUDIENCE", "mission-control-backend"
        )
        self.allowed_azp = azp or {
            x
            for x in os.getenv(
                "MISSION_CONTROL_ALLOWED_AZP", "mission-control-ui,websocket-gateway"
            ).split(",")
            if x
        }
        self._jwks = None
        if self.mode == "required":
            if not self.issuer:
                raise RuntimeError("MISSION_CONTROL_JWT_ISSUER is required when auth is required")
            self._jwks = jwt.PyJWKClient(
                self.issuer + "/protocol/openid-connect/certs", cache_keys=True
            )

    def verify(self, authorization: str | None) -> Principal:
        if self.mode == "disabled":
            return Principal(
                "local-development", frozenset({"Administrator"}), {"auth_mode": "disabled"}
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError("missing_bearer_token")
        token = authorization[7:].strip()
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except Exception as exc:
            raise AuthError("invalid_bearer_token") from exc
        azp = claims.get("azp")
        if self.allowed_azp and azp not in self.allowed_azp:
            raise AuthError("azp_not_allowed", 403)
        roles = set(claims.get("realm_access", {}).get("roles", []))
        roles.update(claims.get("resource_access", {}).get("mission-control", {}).get("roles", []))
        return Principal(str(claims["sub"]), frozenset(roles), claims)

    def require(self, authorization: str | None, permission: str) -> Principal:
        p = self.verify(authorization)
        if not p.allows(permission):
            raise AuthError("insufficient_permission", 403)
        return p
