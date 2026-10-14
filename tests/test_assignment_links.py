from mission_control.assignment_links import AssignmentLink, AssignmentLinkGenerator


def test_task_assignment_link_is_copyable_and_scoped():
    link = AssignmentLinkGenerator("https://mission.example").generate(
        AssignmentLink("Middleware-", "API", "Contracts", "T-14", "codex-04")
    )
    assert (
        link
        == "https://mission.example/assign?repository=Middleware-&area=API&subarea=Contracts&task_id=T-14&agent_id=codex-04"
    )


def test_section_link_can_exist_before_agent_assignment():
    link = AssignmentLinkGenerator("https://mission.example").section_link("Middleware-", "Core")
    assert "repository=Middleware-" in link and "area=Core" in link
