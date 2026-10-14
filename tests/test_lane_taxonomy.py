import pytest

from mission_control.lane_taxonomy import (
    AgentLane,
    TaskLifecycle,
    WorkSection,
    required_lane,
    validate_transition,
)


def test_sections_cover_dashboard_work_views():
    assert {"CORE","FEATURES","API","URLS_ROUTES","WORKSTATIONS","DELIVERY_CI"}.issubset({s.value for s in WorkSection})

def test_lifecycle_separates_implementation_review_test_and_certification():
    validate_transition(TaskLifecycle.IMPLEMENTATION_DONE, TaskLifecycle.REVIEW_REQUIRED)
    validate_transition(TaskLifecycle.REVIEW_DONE, TaskLifecycle.TEST_REQUIRED)
    validate_transition(TaskLifecycle.TEST_DONE, TaskLifecycle.CERTIFICATION_PENDING)
    validate_transition(TaskLifecycle.CERTIFIED, TaskLifecycle.COMPLETED)
    assert required_lane(TaskLifecycle.REVIEW_REQUIRED) is AgentLane.REVIEW
    assert required_lane(TaskLifecycle.TEST_REQUIRED) is AgentLane.TESTING

def test_cannot_skip_evidence_gates():
    with pytest.raises(ValueError):
        validate_transition(TaskLifecycle.IMPLEMENTATION_DONE, TaskLifecycle.COMPLETED)
    with pytest.raises(ValueError):
        validate_transition(TaskLifecycle.TEST_DONE, TaskLifecycle.COMPLETED)

def test_replacement_needed_can_resume_implementation():
    validate_transition(TaskLifecycle.REPLACEMENT_NEEDED, TaskLifecycle.ACTIVE)
