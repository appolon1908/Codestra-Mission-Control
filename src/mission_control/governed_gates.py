"""Read-only integration port for independently accepted controller verification.

The provider is a privileged composition dependency, never author JSON, environment
configuration or a saved observation. Its implementation must reacquire registered
routing, authenticated authority, owned lease, dependencies, current refs/reviews/CI
and independent certificate provenance on EVERY call. This module issues no
certificate, lease, grant or GitHub status. Typed fixture observations authenticate
nothing; no live provider is supplied until the controller owner accepts one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


@dataclass(frozen=True)
class PromotionBinding:
    mission_id: str
    product: str
    repository: str
    pr_number: int
    head_branch: str
    base_branch: str
    head_sha: str
    base_sha: str
    target_sha: str

    @classmethod
    def from_snapshot(cls, mission, repository, snapshot) -> PromotionBinding:
        return cls(
            mission_id=mission["mission_id"],
            product=mission["repository"],
            repository=repository["full_name"] if repository else "",
            **{key: snapshot[key] for key in (
                "pr_number", "head_branch", "base_branch", "head_sha", "base_sha", "target_sha"
            )},
        )


@dataclass(frozen=True)
class ControllerGateObservation:
    binding: PromotionBinding
    observed_at: datetime
    expires_at: datetime
    routing_verified: bool
    controller_verified: bool


class ControllerGateProvider(Protocol):
    def verify(self, binding: PromotionBinding) -> ControllerGateObservation:
        """Independently reacquire current accepted authority; never return cached proof."""
        ...


def controller_gate_blockers(
    provider: ControllerGateProvider | None, binding: PromotionBinding
) -> dict[str, str]:
    def denied(reason):
        return {"ROUTING": "ROUTING_UNVERIFIED", "CONTROLLER": reason}

    if provider is None:
        return denied("CONTROLLER_VERIFICATION_UNCONFIGURED")
    if (
        type(binding) is not PromotionBinding
        or not isinstance(binding.repository, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", binding.repository)
        or type(binding.pr_number) is not int or binding.pr_number <= 0
        or not all(isinstance(v, str) and v for v in (
            binding.mission_id, binding.product, binding.head_branch, binding.base_branch
        ))
        or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{40}", v) for v in (
            binding.head_sha, binding.base_sha, binding.target_sha
        ))
    ):
        return denied("CONTROLLER_BINDING_INVALID")
    try:
        observation = provider.verify(binding)
    except Exception:  # noqa: BLE001 -- trust boundary: deny without leaking provider secrets
        # Privileged providers may handle credentials: never expose exception text.
        return denied("CONTROLLER_VERIFICATION_UNAVAILABLE")
    if type(observation) is not ControllerGateObservation:
        return denied("CONTROLLER_OBSERVATION_INVALID")
    if type(observation.binding) is not PromotionBinding or observation.binding != binding:
        return denied("CONTROLLER_BINDING_MISMATCH")
    if (
        type(observation.routing_verified) is not bool
        or type(observation.controller_verified) is not bool
    ):
        return denied("CONTROLLER_OBSERVATION_INVALID")
    try:
        # Read the clock after provider I/O, so expired responses cannot clear gates.
        now = datetime.now(UTC)
        observed, expires = observation.observed_at, observation.expires_at
        if (
            not isinstance(observed, datetime) or observed.utcoffset() is None
            or not isinstance(expires, datetime) or expires.utcoffset() is None
        ):
            return denied("CONTROLLER_OBSERVATION_INVALID")
        # Bound elapsed time, not local wall time across a daylight-saving change.
        observed, expires = observed.astimezone(UTC), expires.astimezone(UTC)
        if not observed <= now < expires <= observed + timedelta(minutes=5):
            return denied("CONTROLLER_OBSERVATION_STALE")
    except (TypeError, ValueError, OverflowError):
        return denied("CONTROLLER_OBSERVATION_INVALID")
    blockers = {}
    if not observation.routing_verified:
        blockers["ROUTING"] = "ROUTING_UNVERIFIED"
    if not observation.controller_verified:
        blockers["CONTROLLER"] = "CONTROLLER_UNVERIFIED"
    return blockers
