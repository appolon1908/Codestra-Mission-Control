from mission_control.development_guard import DevelopmentEvidence, DevelopmentGuard, GuardState

def good(**overrides):
    data=dict(task_id="T1",local_head="h1",expected_base_sha="b1",remote_base_sha="b1",
              branch="mission/t1",upstream="origin/mission/t1",dirty_count=0,
              headers={"X-Codestra-Service":"middleware"},required_headers={"X-Codestra-Service":"middleware"},
              ci_required=("governance","tests"),ci_green=("governance","tests"),heartbeat_current=True)
    data.update(overrides); return DevelopmentEvidence(**data)

def test_answers_core_tracking_questions():
    answer=DevelopmentGuard().evaluate(good())
    assert answer.state is GuardState.CLEAR
    assert answer.questions["can_work_continue"]=="yes"
    assert all(answer.questions[k]=="yes" for k in ("is_base_current","is_lane_clean","are_headers_correct","is_ci_green","is_agent_covered"))

def test_stale_header_and_ci_fail_closed():
    answer=DevelopmentGuard().evaluate(good(remote_base_sha="b2",headers={},ci_green=("tests",)))
    assert answer.state is GuardState.BLOCKED
    assert "stale_base_sha" in answer.blockers
    assert "wrong_header:X-Codestra-Service" in answer.blockers
    assert "ci_not_green:governance" in answer.blockers

def test_stopped_agent_requires_replacement():
    answer=DevelopmentGuard().evaluate(good(heartbeat_current=False,replacement_assigned=False))
    assert answer.state is GuardState.REPLACEMENT_NEEDED
    assert answer.questions["is_agent_covered"]=="no"
