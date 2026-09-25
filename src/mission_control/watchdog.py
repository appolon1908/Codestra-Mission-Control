from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import MissionStatus
from .store import MissionStore

BLOCKED_STATUSES = frozenset({MissionStatus.BLOCKED, MissionStatus.NEEDS_DECISION})
DISPATCHABLE_STATUSES = frozenset({MissionStatus.READY, MissionStatus.QUEUED})

LEVEL_REMINDER = 1
LEVEL_ESCALATED = 2
LEVEL_OWNER_DECISION = 3
LEVEL_NAMES = {
    LEVEL_REMINDER: "REMINDER",
    LEVEL_ESCALATED: "ESCALATED",
    LEVEL_OWNER_DECISION: "OWNER_DECISION",
}


@dataclass(frozen=True)
class EscalationPolicy:
    """Age thresholds for watchdog conditions.

    Escalations are recorded locally only; external delivery (SMS, email, calls)
    stays disabled until an explicit approval-gated channel exists.
    """

    stale_heartbeat_seconds: int = 300
    escalate_after_seconds: int = 900
    owner_decision_after_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.stale_heartbeat_seconds < 1:
            raise ValueError("stale_heartbeat_seconds must be positive")
        if not (0 < self.escalate_after_seconds < self.owner_decision_after_seconds):
            raise ValueError(
                "escalate_after_seconds must be positive and below owner_decision_after_seconds"
            )

    def level_for(self, age_seconds: float) -> int:
        if age_seconds >= self.owner_decision_after_seconds:
            return LEVEL_OWNER_DECISION
        if age_seconds >= self.escalate_after_seconds:
            return LEVEL_ESCALATED
        return LEVEL_REMINDER


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age(now: datetime, value: str | None) -> float | None:
    parsed = _parse(value)
    return None if parsed is None else max(0.0, (now - parsed).total_seconds())


def escalation_payload(row) -> dict:
    item = dict(row)
    item["detail"] = json.loads(item.pop("detail_json") or "{}")
    item["level_name"] = LEVEL_NAMES.get(int(item["level"]), "UNKNOWN")
    return item


def dispatch_payload(row) -> dict:
    item = dict(row)
    item["takeover"] = bool(item["takeover"])
    return item


class WatchdogMonitor:
    """Read model over the mission ledger for the 24/7 watchdog."""

    def __init__(
        self,
        store: MissionStore,
        *,
        workers: Iterable[object] = (),
        policy: EscalationPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.workers = tuple(workers)
        self.policy = policy or EscalationPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def lease_states(self) -> list[dict]:
        now = self.clock()
        items: list[dict] = []
        for row in self.store.list_leases():
            lease = dict(row)
            expires = _parse(lease["expires_at"])
            heartbeat_age = _age(now, lease["heartbeat_at"]) or 0.0
            if expires is not None and expires <= now:
                state = "EXPIRED"
            elif heartbeat_age >= self.policy.stale_heartbeat_seconds:
                state = "STALE"
            else:
                state = "ACTIVE"
            lease["state"] = state
            lease["heartbeat_age_seconds"] = round(heartbeat_age, 3)
            lease["expires_in_seconds"] = (
                round((expires - now).total_seconds(), 3) if expires else None
            )
            execution = self.store.latest_agent_execution_for(
                lease["mission_id"],
                lease["agent_id"],
            )
            lease["execution"] = (
                {
                    "execution_id": execution["execution_id"],
                    "provider": execution["provider"],
                    "state": execution["state"],
                    "runner_pid": execution["runner_pid"],
                    "worktree": execution["worktree"],
                }
                if execution
                else None
            )
            items.append(lease)
        return items

    def stale_leases(self) -> list[dict]:
        return [item for item in self.lease_states() if item["state"] != "ACTIVE"]

    def active_workers(self) -> dict:
        leases = self.lease_states()
        by_agent: dict[str, list[dict]] = {}
        for lease in leases:
            by_agent.setdefault(lease["agent_id"], []).append(lease)

        slots: list[dict] = []
        managed: set[str] = set()
        for worker in self.workers:
            agent_id = worker.agent_id  # type: ignore[attr-defined]
            managed.add(agent_id)
            held = by_agent.get(agent_id, [])
            live = [lease for lease in held if lease["state"] != "EXPIRED"]
            if not worker.enabled:  # type: ignore[attr-defined]
                state = "DISABLED"
            elif not live:
                state = "IDLE"
            elif any(lease["state"] == "STALE" for lease in live):
                state = "STALE"
            else:
                state = "ACTIVE"
            slots.append(
                {
                    "agent_id": agent_id,
                    "provider": worker.provider,  # type: ignore[attr-defined]
                    "enabled": bool(worker.enabled),  # type: ignore[attr-defined]
                    "state": state,
                    "leases": held,
                }
            )
        unmanaged = [
            lease
            for agent_id, held in sorted(by_agent.items())
            if agent_id not in managed
            for lease in held
        ]
        return {
            "workers": slots,
            "active": sum(1 for slot in slots if slot["state"] in {"ACTIVE", "STALE"}),
            "idle": sum(1 for slot in slots if slot["state"] == "IDLE"),
            "unmanaged_leases": unmanaged,
        }

    def blocked_lanes(self) -> list[dict]:
        now = self.clock()
        lanes: list[dict] = []
        for row in self.store.list_missions():
            mission = dict(row)
            status = MissionStatus(mission["status"])
            if status not in BLOCKED_STATUSES:
                continue
            checkpoint = self.store.latest_checkpoint(mission["mission_id"])
            lanes.append(
                {
                    "mission_id": mission["mission_id"],
                    "repository": mission["repository"],
                    "status": status.value,
                    "branch": mission["branch"],
                    "worktree": mission["worktree"],
                    "head_sha": mission["head_sha"],
                    "blocked_since": mission["updated_at"],
                    "age_seconds": round(_age(now, mission["updated_at"]) or 0.0, 3),
                    "last_checkpoint": (
                        {
                            "agent_id": checkpoint["agent_id"],
                            "state": checkpoint["state"],
                            "head_sha": checkpoint["head_sha"],
                            "dirty_count": checkpoint["dirty_count"],
                            "blockers": json.loads(checkpoint["blockers_json"] or "[]"),
                            "created_at": checkpoint["created_at"],
                        }
                        if checkpoint
                        else None
                    ),
                }
            )
        return sorted(lanes, key=lambda lane: (-lane["age_seconds"], lane["mission_id"]))

    def conditions(self) -> list[dict]:
        now = self.clock()
        policy = self.policy
        found: list[dict] = []
        leased_missions: set[str] = set()
        for lease in self.lease_states():
            leased_missions.add(lease["mission_id"])
            if lease["state"] == "ACTIVE":
                continue
            kind = "EXPIRED_LEASE" if lease["state"] == "EXPIRED" else "STALE_HEARTBEAT"
            # Staleness counts from the missed heartbeat window, so a fresh STALE
            # lease starts at REMINDER rather than jumping straight to ESCALATED.
            age = max(0.0, lease["heartbeat_age_seconds"] - policy.stale_heartbeat_seconds)
            found.append(
                {
                    "mission_id": lease["mission_id"],
                    "kind": kind,
                    "agent_id": lease["agent_id"],
                    "level": policy.level_for(age),
                    "detail": {
                        "heartbeat_at": lease["heartbeat_at"],
                        "expires_at": lease["expires_at"],
                        "heartbeat_age_seconds": lease["heartbeat_age_seconds"],
                        "execution": lease["execution"],
                    },
                }
            )
        for lane in self.blocked_lanes():
            checkpoint = lane["last_checkpoint"] or {}
            found.append(
                {
                    "mission_id": lane["mission_id"],
                    "kind": f"{lane['status']}_LANE",
                    "agent_id": checkpoint.get("agent_id"),
                    "level": policy.level_for(lane["age_seconds"]),
                    "detail": {
                        "blocked_since": lane["blocked_since"],
                        "blockers": checkpoint.get("blockers", []),
                        "head_sha": lane["head_sha"],
                    },
                }
            )
        missions = {row["mission_id"]: row for row in self.store.list_missions()}
        for mission_id, dispatch in self.store.latest_watchdog_dispatches().items():
            mission = missions.get(mission_id)
            if dispatch["state"] != "NOT_DISPATCHED" or mission is None:
                continue
            if mission_id in leased_missions:
                continue
            if MissionStatus(mission["status"]) not in DISPATCHABLE_STATUSES:
                continue
            found.append(
                {
                    "mission_id": mission_id,
                    "kind": "DISPATCH_FAILED",
                    "agent_id": dispatch["agent_id"],
                    "level": policy.level_for(_age(now, dispatch["created_at"]) or 0.0),
                    "detail": {
                        "dispatch_id": dispatch["id"],
                        "reason": dispatch["reason"],
                        "failed_at": dispatch["created_at"],
                    },
                }
            )
        return found

    def evaluate_escalations(self) -> dict:
        observed_at = self.clock().isoformat()
        changes = self.store.sync_watchdog_escalations(
            self.conditions(),
            observed_at=observed_at,
        )
        return {"observed_at": observed_at, **changes}

    def escalations(self, *, states: tuple[str, ...] | None = ("OPEN", "ACKNOWLEDGED")) -> list:
        return [
            escalation_payload(row) for row in self.store.list_watchdog_escalations(states=states)
        ]

    def dispatches(
        self,
        *,
        mission_id: str | None = None,
        takeover_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        return [
            dispatch_payload(row)
            for row in self.store.list_watchdog_dispatches(
                mission_id=mission_id,
                takeover_only=takeover_only,
                limit=limit,
            )
        ]

    def snapshot(self) -> dict:
        workers = self.active_workers()
        return {
            "generated_at": self.clock().isoformat(),
            "policy": {
                "stale_heartbeat_seconds": self.policy.stale_heartbeat_seconds,
                "escalate_after_seconds": self.policy.escalate_after_seconds,
                "owner_decision_after_seconds": self.policy.owner_decision_after_seconds,
                "external_delivery": "DISABLED",
            },
            "workers": workers,
            "stale_leases": self.stale_leases(),
            "blocked_lanes": self.blocked_lanes(),
            "escalations": self.escalations(),
            "successor_dispatches": self.dispatches(takeover_only=True, limit=20),
        }
