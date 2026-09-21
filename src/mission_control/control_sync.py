from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Surface(StrEnum):
    LOCAL = "local"
    LINEAR = "linear"
    GITHUB = "github"
    NOTION = "notion"


class SyncState(StrEnum):
    BLOCKED = "BLOCKED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    COMPLETE_ALLOWED = "COMPLETE_ALLOWED"


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


@dataclass(frozen=True)
class SyncDecision:
    state: SyncState
    can_complete: bool
    missing: tuple[Surface, ...]
    failed: tuple[Surface, ...]
    stale: tuple[Surface, ...]
    reason: str


class ControlSurfaceAdapter(Protocol):
    surface: Surface

    def read_state(self, mission_id: str) -> SurfaceObservation:
        ...

    def publish_checkpoint(self, checkpoint: CheckpointEnvelope) -> SurfaceObservation:
        ...


def reconcile(
    checkpoint: CheckpointEnvelope,
    observations: list[SurfaceObservation],
    *,
    required: tuple[Surface, ...] = (
        Surface.LOCAL,
        Surface.LINEAR,
        Surface.GITHUB,
        Surface.NOTION,
    ),
) -> SyncDecision:
    by_surface = {item.surface: item for item in observations}
    missing = tuple(
        surface
        for surface in required
        if surface not in by_surface or not by_surface[surface].available
    )
    failed = tuple(
        surface
        for surface, item in by_surface.items()
        if item.available and (item.error or item.status.upper() in {"ERROR", "FAILED"})
    )

    stale_list: list[Surface] = []
    if checkpoint.head_sha:
        for surface in (Surface.LOCAL, Surface.GITHUB):
            item = by_surface.get(surface)
            if item and item.available and item.head_sha and item.head_sha != checkpoint.head_sha:
                stale_list.append(surface)
    stale = tuple(stale_list)

    if missing or failed or stale:
        reasons: list[str] = []
        if missing:
            reasons.append("missing=" + ",".join(x.value for x in missing))
        if failed:
            reasons.append("failed=" + ",".join(x.value for x in failed))
        if stale:
            reasons.append("stale_sha=" + ",".join(x.value for x in stale))
        return SyncDecision(
            SyncState.BLOCKED,
            False,
            missing,
            failed,
            stale,
            "; ".join(reasons),
        )

    if checkpoint.request_complete:
        return SyncDecision(
            SyncState.COMPLETE_ALLOWED,
            True,
            (),
            (),
            (),
            "all required surfaces are available and exact-SHA aligned",
        )

    return SyncDecision(
        SyncState.READY_FOR_REVIEW,
        False,
        (),
        (),
        (),
        "checkpoint synchronized; completion was not requested",
    )


class CheckpointFanout:
    def __init__(self, adapters: list[ControlSurfaceAdapter]) -> None:
        self.adapters = adapters

    def publish(self, checkpoint: CheckpointEnvelope) -> SyncDecision:
        observations: list[SurfaceObservation] = []
        for adapter in self.adapters:
            try:
                observations.append(adapter.publish_checkpoint(checkpoint))
            except Exception as exc:
                observations.append(
                    SurfaceObservation(
                        surface=adapter.surface,
                        available=False,
                        status="ERROR",
                        error=type(exc).__name__,
                    )
                )
        return reconcile(checkpoint, observations)
