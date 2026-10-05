from mission_control.continuous_flow import (
    ContinuousFlow,
    FlowAction,
    FlowContext,
    required_test_profile,
)


def test_implementation_continues_then_hands_to_review():
    flow = ContinuousFlow()
    assert (
        flow.decide(FlowContext(lane="IMPLEMENTATION")).action is FlowAction.CONTINUE_IMPLEMENTATION
    )
    assert (
        flow.decide(FlowContext(lane="IMPLEMENTATION", implementation_done=True)).action
        is FlowAction.HANDOFF_REVIEW
    )


def test_safe_failures_auto_remediate_without_idle_wait():
    d = ContinuousFlow().decide(
        FlowContext(
            lane="IMPLEMENTATION",
            safe_remediations=("stale_base_sha", "wrong_header", "missing_test", "failing_gate"),
        )
    )
    assert d.action is FlowAction.AUTO_REMEDIATE


def test_review_and_testing_are_distinct_automatic_handoffs():
    flow = ContinuousFlow()
    assert (
        flow.decide(FlowContext(lane="REVIEW", review_done=True)).action
        is FlowAction.HANDOFF_TESTING
    )
    assert (
        flow.decide(FlowContext(lane="TESTING", testing_done=True)).action
        is FlowAction.CERTIFICATION
    )


def test_api_testing_requires_postman_and_postgres_when_applicable():
    profile = required_test_profile(has_api=True, uses_postgres=True)
    assert profile.postman and profile.postgres and profile.api_contract


def test_privileged_changes_never_auto_cross_authority_boundary():
    assert (
        ContinuousFlow().decide(FlowContext(lane="IMPLEMENTATION", production_effect=True)).action
        is FlowAction.HUMAN_GATE
    )
    assert (
        ContinuousFlow().decide(FlowContext(lane="IMPLEMENTATION", destructive_change=True)).action
        is FlowAction.HUMAN_GATE
    )
