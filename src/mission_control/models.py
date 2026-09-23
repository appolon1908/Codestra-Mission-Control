
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
    VERIFYING = "VERIFYING"
    MERGE_COORDINATING = "MERGE_COORDINATING"
    CONFLICT = "CONFLICT"
    MERGE_READY = "MERGE_READY"
    MERGED = "MERGED"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    NEEDS_DECISION = "NEEDS_DECISION"
    STAGING = "STAGING"
    CERTIFIED = "CERTIFIED"
    COMPLETE = "COMPLETE"


class AgentRole(StrEnum):
    WRITER = "WRITER"
    REVIEWER = "REVIEWER"
    VERIFIER = "VERIFIER"
    MERGE_COORDINATOR = "MERGE_COORDINATOR"


class ApprovalGate(StrEnum):
    REVIEW = "REVIEW"
    VERIFICATION = "VERIFICATION"
    MERGE_AUTHORIZATION = "MERGE_AUTHORIZATION"


class ApprovalStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class ConflictClass(StrEnum):
    NONE = "CLASS-0"
    MECHANICAL = "CLASS-1"
    SEMANTIC = "CLASS-2"
    CROSS_REPO = "CLASS-3"
    UNKNOWN = "CLASS-4"


class ConflictStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class MergeQueueState(StrEnum):
    QUEUED = "QUEUED"
    WAITING_REVIEW = "WAITING_REVIEW"
    WAITING_VERIFICATION = "WAITING_VERIFICATION"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    BLOCKED_CONFLICT = "BLOCKED_CONFLICT"
    BLOCKED_CI = "BLOCKED_CI"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    READY = "READY"
    MERGING = "MERGING"
    MERGED = "MERGED"
    FAILED = "FAILED"


class DispatchState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


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
