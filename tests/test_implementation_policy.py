import pytest
from mission_control.implementation_policy import ImplementationPolicy


def test_implementation_agents_require_atomic_leased_lane():
    policy = ImplementationPolicy()
    policy.validate_assignment(
        agent_type="codex", task_id="T1", lease_present=True, branch="mission/t1", worktree="/wt"
    )
    with pytest.raises(ValueError, match="review-only"):
        policy.validate_assignment(
            agent_type="claude",
            task_id="T1",
            lease_present=True,
            branch="mission/t1",
            worktree="/wt",
            review_only=True,
        )
    with pytest.raises(ValueError, match="atomic task"):
        policy.validate_assignment(
            agent_type="grok", task_id=None, lease_present=False, branch=None, worktree=None
        )


def test_supervisor_is_not_implementation_lane():
    ImplementationPolicy().validate_assignment(
        agent_type="repo-supervisor", task_id=None, lease_present=False, branch=None, worktree=None
    )


def test_workstation_roles_separate_development_and_publication():
    rows = {r.workstation: r for r in ImplementationPolicy.workstation_defaults()}
    assert (
        rows["UBUNTU_DESKTOP"].implementation_allowed
        and not rows["UBUNTU_DESKTOP"].publication_allowed
    )
    assert (
        rows["APPOLON_LAPTOP"].publication_allowed
        and not rows["APPOLON_LAPTOP"].implementation_allowed
    )
