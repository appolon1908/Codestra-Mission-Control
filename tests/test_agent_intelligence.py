from mission_control.agent_intelligence import AgentScoreInput, rate_agent


def test_verified_delivery_outranks_unpushed_volume():
    verified = rate_agent(
        AgentScoreInput(96, 96, 95, 94, 90, 94, complexity_class="C3", confidence=95)
    )
    unpushed = rate_agent(
        AgentScoreInput(42, 84, 58, 50, 98, 40, complexity_class="C3", confidence=90)
    )
    assert verified["avi"] > 90
    assert unpushed["avi"] < verified["avi"]


def test_external_blocker_does_not_force_low_reliability():
    score = rate_agent(
        AgentScoreInput(82, 92, 96, 85, 72, 88, complexity_class="C4", confidence=90)
    )
    assert score["avi"] >= 85
    assert score["band"] in {"Strong", "Excellent", "Exceptional"}


def test_low_confidence_marks_score_provisional():
    score = rate_agent(AgentScoreInput(90, 90, 90, 90, 90, 90, confidence=55))
    assert score["provisional"] is True
