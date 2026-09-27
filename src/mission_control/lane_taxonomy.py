from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AgentLane(StrEnum):
    IMPLEMENTATION = "IMPLEMENTATION"
    REVIEW = "REVIEW"
    TESTING = "TESTING"
    SUPERVISION = "SUPERVISION"


class WorkSection(StrEnum):
    CORE = "CORE"
    FEATURES = "FEATURES"
    API = "API"
    URLS_ROUTES = "URLS_ROUTES"
    WORKSTATIONS = "WORKSTATIONS"
    IDENTITY_SECURITY = "IDENTITY_SECURITY"
    DATA_PERSISTENCE = "DATA_PERSISTENCE"
    INTEGRATIONS_ADAPTERS = "INTEGRATIONS_ADAPTERS"
    OBSERVABILITY_AUDIT = "OBSERVABILITY_AUDIT"
    DELIVERY_CI = "DELIVERY_CI"


class TaskLifecycle(StrEnum):
    READY = "READY"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    TEST_REQUIRED = "TEST_REQUIRED"
    REPLACEMENT_NEEDED = "REPLACEMENT_NEEDED"
    BLOCKED = "BLOCKED"
    IMPLEMENTATION_DONE = "IMPLEMENTATION_DONE"
    REVIEW_DONE = "REVIEW_DONE"
    TEST_DONE = "TEST_DONE"
    CERTIFICATION_PENDING = "CERTIFICATION_PENDING"
    CERTIFIED = "CERTIFIED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class LaneAssignment:
    agent_id: str
    lane: AgentLane
    section: WorkSection
    task_id: str
    state: TaskLifecycle


ALLOWED_TRANSITIONS = {
    TaskLifecycle.READY: {TaskLifecycle.ACTIVE, TaskLifecycle.BLOCKED},
    TaskLifecycle.ACTIVE: {TaskLifecycle.IMPLEMENTATION_DONE, TaskLifecycle.WAITING,
                           TaskLifecycle.REPLACEMENT_NEEDED, TaskLifecycle.BLOCKED},
    TaskLifecycle.REPLACEMENT_NEEDED: {TaskLifecycle.ACTIVE, TaskLifecycle.BLOCKED},
    TaskLifecycle.IMPLEMENTATION_DONE: {TaskLifecycle.REVIEW_REQUIRED},
    TaskLifecycle.REVIEW_REQUIRED: {TaskLifecycle.REVIEW_DONE, TaskLifecycle.ACTIVE},
    TaskLifecycle.REVIEW_DONE: {TaskLifecycle.TEST_REQUIRED},
    TaskLifecycle.TEST_REQUIRED: {TaskLifecycle.TEST_DONE, TaskLifecycle.ACTIVE},
    TaskLifecycle.TEST_DONE: {TaskLifecycle.CERTIFICATION_PENDING},
    TaskLifecycle.CERTIFICATION_PENDING: {TaskLifecycle.CERTIFIED, TaskLifecycle.ACTIVE},
    TaskLifecycle.CERTIFIED: {TaskLifecycle.COMPLETED},
    TaskLifecycle.WAITING: {TaskLifecycle.ACTIVE, TaskLifecycle.REPLACEMENT_NEEDED, TaskLifecycle.BLOCKED},
    TaskLifecycle.BLOCKED: {TaskLifecycle.READY, TaskLifecycle.ACTIVE},
    TaskLifecycle.COMPLETED: set(),
}


def validate_transition(current: TaskLifecycle, target: TaskLifecycle) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid lifecycle transition: {current.value} -> {target.value}")


def required_lane(state: TaskLifecycle) -> AgentLane | None:
    if state is TaskLifecycle.ACTIVE:
        return AgentLane.IMPLEMENTATION
    if state is TaskLifecycle.REVIEW_REQUIRED:
        return AgentLane.REVIEW
    if state is TaskLifecycle.TEST_REQUIRED:
        return AgentLane.TESTING
    return None
