from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class Surface(StrEnum):
    LOCAL = "local"
    LINEAR = "linear"
    GITHUB = "github"
    NOTION = "notion"


class SyncState(StrEnum):
    BLOCKED = "BLOCKED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    COMPLETE_ALLOWED = "COMPLETE_ALLOWED"


class SourceFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNREACHABLE = "UNREACHABLE"
    ERROR = "ERROR"


class ConflictKind(StrEnum):
    HEAD_SHA = "HEAD_SHA"
    STATUS = "STATUS"
    OWNERSHIP = "OWNERSHIP"
    SURFACE_MISMATCH = "SURFACE_MISMATCH"


DEFAULT_REQUIRED: tuple[Surface, ...] = (
    Surface.LOCAL,
    Surface.LINEAR,
    Surface.GITHUB,
    Surface.NOTION,
)
FAILED_STATUSES = frozenset({"ERROR", "FAILED"})
TERMINAL_STATUSES = frozenset(
    {"DONE", "COMPLETED", "COMPLETE", "MERGED", "CLOSED", "CANCELED", "CANCELLED"}
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class CheckpointEnvelope:
    mission_id: str
    status: str
    head_sha: str | None
    request_complete: bool = False


@dataclass(frozen=True)
class SurfaceObservation:
    surface: Surface
    available: bool
    status: str
    head_sha: str | None = None
    error: str | None = None
    observed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None

    @property
    def succeeded(self) -> bool:
        return self.available and not self.error and self.status.upper() not in FAILED_STATUSES


@dataclass(frozen=True)
class SourceHealth:
    surface: Surface
    freshness: SourceFreshness
    available: bool
    status: str
    head_sha: str | None
    error: str | None
    observed_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    age_seconds: float | None
    drift: bool
    reconciled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface.value,
            "freshness": self.freshness.value,
            "available": self.available,
            "status": self.status,
            "head_sha": self.head_sha,
            "error": self.error,
            "observed_at": _iso(self.observed_at),
            "last_success_at": _iso(self.last_success_at),
            "last_error_at": _iso(self.last_error_at),
            "age_seconds": self.age_seconds,
            "drift": self.drift,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True)
class SyncConflict:
    kind: ConflictKind
    surfaces: tuple[Surface, ...]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "surfaces": [surface.value for surface in self.surfaces],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SyncDecision:
    state: SyncState
    can_complete: bool
    missing: tuple[Surface, ...]
    failed: tuple[Surface, ...]
    stale: tuple[Surface, ...]
    reason: str
    expired: tuple[Surface, ...] = ()
    conflicts: tuple[SyncConflict, ...] = ()
    sources: tuple[SourceHealth, ...] = ()
    reconciled: tuple[Surface, ...] = ()

    @property
    def drift(self) -> tuple[Surface, ...]:
        """Reachable surfaces whose head SHA differs from the checkpoint."""
        return self.stale

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "can_complete": self.can_complete,
            "reason": self.reason,
            "missing": [surface.value for surface in self.missing],
            "failed": [surface.value for surface in self.failed],
            "drift": [surface.value for surface in self.stale],
            "expired": [surface.value for surface in self.expired],
            "reconciled": [surface.value for surface in self.reconciled],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "sources": [source.to_dict() for source in self.sources],
        }


class ControlSurfaceAdapter(Protocol):
    surface: Surface

    def read_state(self, mission_id: str) -> SurfaceObservation:
        ...

    def publish_checkpoint(self, checkpoint: CheckpointEnvelope) -> SurfaceObservation:
        ...


class SyncLedger(Protocol):
    def record_surface_observation(
        self,
        mission_id: str,
        observation: SurfaceObservation,
        *,
        agent_id: str | None = None,
    ) -> SurfaceObservation:
        ...


OwnerCheck = Callable[[str, str], bool]


def _detect_conflicts(
    checkpoint: CheckpointEnvelope,
    reachable: list[SurfaceObservation],
) -> list[SyncConflict]:
    conflicts: list[SyncConflict] = []
    # Drift from an authoritative checkpoint SHA is reported as drift. A conflict is
    # divergence among the surfaces themselves: two or more distinct non-checkpoint heads.
    divergent = [
        item for item in reachable if item.head_sha and item.head_sha != checkpoint.head_sha
    ]
    heads = {item.head_sha for item in divergent if item.head_sha}
    if len(heads) > 1:
        conflicts.append(
            SyncConflict(
                ConflictKind.HEAD_SHA,
                tuple(item.surface for item in divergent),
                "reachable surfaces disagree on head_sha: " + ",".join(sorted(heads)),
            )
        )
    checkpoint_terminal = checkpoint.status.upper() in TERMINAL_STATUSES
    if not checkpoint.request_complete and not checkpoint_terminal:
        terminal = tuple(
            item.surface for item in reachable if item.status.upper() in TERMINAL_STATUSES
        )
        if terminal:
            conflicts.append(
                SyncConflict(
                    ConflictKind.STATUS,
                    terminal,
                    f"surface reports terminal status while checkpoint is {checkpoint.status}",
                )
            )
    return conflicts


def reconcile(
    checkpoint: CheckpointEnvelope,
    observations: list[SurfaceObservation],
    *,
    required: tuple[Surface, ...] = DEFAULT_REQUIRED,
    now: datetime | None = None,
    max_age: timedelta | None = None,
    extra_conflicts: tuple[SyncConflict, ...] = (),
) -> SyncDecision:
    """Reconcile per-surface observations against a checkpoint.

    Each reachable source is evaluated independently, so an unreachable or failing
    surface blocks completion without hiding whether the other surfaces are aligned.
    """
    now = now or utc_now()
    by_surface = {item.surface: item for item in observations}
    ordered = list(required) + [s for s in by_surface if s not in required]

    missing = tuple(
        surface
        for surface in required
        if surface not in by_surface or not by_surface[surface].available
    )
    failed = tuple(
        surface
        for surface in ordered
        if surface in by_surface
        and by_surface[surface].available
        and not by_surface[surface].succeeded
    )

    sources: list[SourceHealth] = []
    stale: list[Surface] = []
    expired: list[Surface] = []
    reconciled: list[Surface] = []
    reachable: list[SurfaceObservation] = []
    for surface in ordered:
        item = by_surface.get(surface)
        if item is None:
            continue
        age = (now - item.observed_at).total_seconds() if item.observed_at else None
        drift = bool(
            item.available
            and checkpoint.head_sha
            and item.head_sha
            and item.head_sha != checkpoint.head_sha
        )
        if not item.available:
            freshness = SourceFreshness.UNREACHABLE
        elif not item.succeeded:
            freshness = SourceFreshness.ERROR
        elif max_age is not None and (age is None or age > max_age.total_seconds()):
            freshness = SourceFreshness.STALE
        else:
            freshness = SourceFreshness.FRESH

        if item.available and item.succeeded:
            reachable.append(item)
        if drift:
            stale.append(surface)
        if freshness is SourceFreshness.STALE:
            expired.append(surface)
        is_reconciled = freshness is SourceFreshness.FRESH and not drift
        if is_reconciled:
            reconciled.append(surface)

        last_success = item.last_success_at
        last_error = item.last_error_at
        if item.observed_at and item.succeeded:
            last_success = max(filter(None, (last_success, item.observed_at)))
        elif item.observed_at:
            last_error = max(filter(None, (last_error, item.observed_at)))
        sources.append(
            SourceHealth(
                surface=surface,
                freshness=freshness,
                available=item.available,
                status=item.status,
                head_sha=item.head_sha,
                error=item.error,
                observed_at=item.observed_at,
                last_success_at=last_success,
                last_error_at=last_error,
                age_seconds=age,
                drift=drift,
                reconciled=is_reconciled,
            )
        )

    conflicts = tuple(extra_conflicts) + tuple(_detect_conflicts(checkpoint, reachable))
    common = {
        "expired": tuple(expired),
        "conflicts": conflicts,
        "sources": tuple(sources),
        "reconciled": tuple(reconciled),
    }

    if missing or failed or stale or expired or conflicts:
        reasons: list[str] = []
        if missing:
            reasons.append("missing=" + ",".join(x.value for x in missing))
        if failed:
            reasons.append("failed=" + ",".join(x.value for x in failed))
        if stale:
            reasons.append("stale_sha=" + ",".join(x.value for x in stale))
        if expired:
            reasons.append("expired=" + ",".join(x.value for x in expired))
        if conflicts:
            reasons.append("conflicts=" + ",".join(c.kind.value for c in conflicts))
        return SyncDecision(
            SyncState.BLOCKED,
            False,
            missing,
            failed,
            tuple(stale),
            "; ".join(reasons),
            **common,
        )

    if checkpoint.request_complete:
        return SyncDecision(
            SyncState.COMPLETE_ALLOWED,
            True,
            (),
            (),
            (),
            "all required surfaces are available and exact-SHA aligned",
            **common,
        )

    return SyncDecision(
        SyncState.READY_FOR_REVIEW,
        False,
        (),
        (),
        (),
        "checkpoint synchronized; completion was not requested",
        **common,
    )


class CheckpointFanout:
    """Publish one checkpoint to every bound surface, one adapter per surface.

    Ownership-safe: when ``owner_check`` is configured, only the current writer-lease
    owner may fan out, and a refused publish performs no surface writes. An adapter
    may only report on its own surface; a mismatched report is discarded.
    """

    def __init__(
        self,
        adapters: list[ControlSurfaceAdapter],
        *,
        owner_check: OwnerCheck | None = None,
        ledger: SyncLedger | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        seen: set[Surface] = set()
        for adapter in adapters:
            if adapter.surface in seen:
                raise ValueError(f"duplicate adapter for surface {adapter.surface.value}")
            seen.add(adapter.surface)
        self.adapters = adapters
        self.owner_check = owner_check
        self.ledger = ledger
        self.clock = clock

    def publish(
        self,
        checkpoint: CheckpointEnvelope,
        *,
        agent_id: str | None = None,
        required: tuple[Surface, ...] = DEFAULT_REQUIRED,
        max_age: timedelta | None = None,
    ) -> SyncDecision:
        if self.owner_check is not None and (
            not agent_id or not self.owner_check(checkpoint.mission_id, agent_id)
        ):
            conflict = SyncConflict(
                ConflictKind.OWNERSHIP,
                tuple(adapter.surface for adapter in self.adapters),
                f"{agent_id or 'anonymous'} does not own {checkpoint.mission_id}; "
                "fanout refused",
            )
            return reconcile(
                checkpoint,
                [],
                required=required,
                now=self.clock(),
                max_age=max_age,
                extra_conflicts=(conflict,),
            )

        observations: list[SurfaceObservation] = []
        conflicts: list[SyncConflict] = []
        for adapter in self.adapters:
            try:
                observation = adapter.publish_checkpoint(checkpoint)
            except Exception as exc:  # noqa: BLE001
                observation = SurfaceObservation(
                    surface=adapter.surface,
                    available=False,
                    status="ERROR",
                    error=type(exc).__name__,
                )
            if observation.surface != adapter.surface:
                conflicts.append(
                    SyncConflict(
                        ConflictKind.SURFACE_MISMATCH,
                        (adapter.surface, observation.surface),
                        f"{adapter.surface.value} adapter reported "
                        f"{observation.surface.value}; discarded",
                    )
                )
                observation = SurfaceObservation(
                    surface=adapter.surface,
                    available=False,
                    status="ERROR",
                    error="SurfaceMismatch",
                )
            if observation.observed_at is None:
                observation = replace(observation, observed_at=self.clock())
            if self.ledger is not None:
                observation = self.ledger.record_surface_observation(
                    checkpoint.mission_id,
                    observation,
                    agent_id=agent_id,
                )
            observations.append(observation)
        return reconcile(
            checkpoint,
            observations,
            required=required,
            now=self.clock(),
            max_age=max_age,
            extra_conflicts=tuple(conflicts),
        )
