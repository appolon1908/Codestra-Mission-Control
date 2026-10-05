from mission_control.flow_orchestrator import FlowOrchestrator
from mission_control.lane_taxonomy import AgentLane
from mission_control.continuous_flow import FlowAction
from mission_control.mission_router import AtomicTask, RankedTask


def test_implementation_completion_hands_off_and_requests_next_task():
    h = FlowOrchestrator().on_checkpoint(
        task_id="T1", lane=AgentLane.IMPLEMENTATION, implementation_done=True
    )
    assert h.to_lane is AgentLane.REVIEW
    assert h.release_current_agent and h.request_next_task


def test_review_completion_hands_to_testing_and_releases_reviewer():
    h = FlowOrchestrator().on_checkpoint(task_id="T1", lane=AgentLane.REVIEW, review_done=True)
    assert h.to_lane is AgentLane.TESTING
    assert h.request_next_task


def test_testing_completion_enters_certification_and_frees_tester():
    h = FlowOrchestrator().on_checkpoint(task_id="T1", lane=AgentLane.TESTING, testing_done=True)
    assert h.action is FlowAction.CERTIFICATION
    assert h.release_current_agent and h.request_next_task


def test_safe_failure_stays_with_agent_for_automatic_fix():
    h = FlowOrchestrator().on_checkpoint(
        task_id="T1", lane=AgentLane.IMPLEMENTATION, safe_remediations=("missing_test",)
    )
    assert h.action is FlowAction.AUTO_REMEDIATE
    assert not h.release_current_agent


def test_next_ready_excludes_completed_task():
    a = RankedTask(AtomicTask("T1", "r", "a", "s", "m"), 10, ())
    b = RankedTask(AtomicTask("T2", "r", "a", "s", "m"), 9, ())
    assert FlowOrchestrator.next_ready_task([a, b], exclude_task_id="T1").task.task_id == "T2"
