
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


class MissionStatus(StrEnum):
    QUEUED = "QUEUED"
    READY = "READY"
    WORKING = "WORKING"
    IN_REVIEW = "IN_REVIEW"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    NEEDS_DECISION = "NEEDS_DECISION"
    MERGE_READY = "MERGE_READY"
    STAGING = "STAGING"
    CERTIFIED = "CERTIFIED"
    COMPLETE = "COMPLETE"


class AgentRole(StrEnum):
    WRITER = "WRITER"
    REVIEWER = "REVIEWER"
    VERIFIER = "VERIFIER"


class ApprovalLevel(IntEnum):
    READ_ONLY = 0
    LOCAL_WRITE = 1
    BRANCH_PUSH = 2
    MERGE = 3
    STAGING_MUTATION = 4
    PRODUCTION_EFFECT = 5


@dataclass(frozen=True)
class Mission:
    mission_id: str
    repository: str
    goal: str
    status: MissionStatus = MissionStatus.READY
    branch: str | None = None
    worktree: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    required_approval: ApprovalLevel = ApprovalLevel.LOCAL_WRITE
    acceptance: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Lease:
    mission_id: str
    agent_id: str
    role: AgentRole
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    @property
    def expired(self) -> bool:
        return utcnow() >= self.expires_at


@dataclass(frozen=True)
class Checkpoint:
    mission_id: str
    agent_id: str
    state: str
    head_sha: str | None = None
    dirty_count: int | None = None
    tests: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    next_task_requested: bool = False


@dataclass(frozen=True)
class Approval:
    mission_id: str
    level: ApprovalLevel
    actor: str
    status: str
    created_at: datetime
