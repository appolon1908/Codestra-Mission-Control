from mission_control.models import Mission
from mission_control.store import MissionStore


def make_store(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-249", "repo", "implement builder"))
    store.upsert_mission(Mission("PAS-29", "repo", "implement dispatcher"))
    return store


def test_agent_numbers_are_monotonic_and_globally_unique(tmp_path):
    store = make_store(tmp_path)
    first = store.start_implementation_execution(
        execution_id="impl-1",
        mission_id="PAS-249",
        agent_id="codex-01",
        workstation="codestra-desktop",
        provider="codex",
        branch="mission/one",
        worktree="/tmp/one",
    )
    second = store.start_implementation_execution(
        execution_id="impl-2",
        mission_id="PAS-29",
        agent_id="claude-02",
        workstation="appolon",
        provider="claude",
        branch="mission/two",
        worktree="/tmp/two",
    )
    assert first == 1
    assert second == 2
    rows = store.list_implementation_executions()
    assert [row["agent_number"] for row in rows] == [1, 2]


def test_proof_is_persisted_with_matching_delivery_shas(tmp_path):
    store = make_store(tmp_path)
    store.start_implementation_execution(
        execution_id="impl-proof",
        mission_id="PAS-249",
        agent_id="codex-03",
        workstation="codestra-desktop",
        provider="codex",
        branch="mission/proof",
        worktree="/tmp/proof",
        api_required=True,
    )
    decision = store.record_implementation_proof(
        "impl-proof",
        implementation_files=["src/mission_control/api.py"],
        api_endpoints=["POST /platform/v1/agent-executions"],
        tests={"status": "PASS", "total": 9, "failed": 0},
        local_commit_sha="deadbeef",
        pushed_branch_sha="deadbeef",
        pr_number=42,
        pr_url="https://github.com/example/repo/pull/42",
        pr_head_sha="deadbeef",
    )
    assert decision.eligible_for_review
    row = store.get_implementation_execution("impl-proof")
    assert row is not None
    assert row["state"] == "PROVEN"
    assert row["proof_matched"] == 1
    assert row["local_commit_sha"] == row["pushed_branch_sha"] == row["pr_head_sha"]


def test_mismatched_delivery_is_persisted_as_needs_rework(tmp_path):
    store = make_store(tmp_path)
    store.start_implementation_execution(
        execution_id="impl-bad",
        mission_id="PAS-249",
        agent_id="codex-04",
        workstation="codestra-desktop",
        provider="codex",
        branch="mission/bad",
        worktree="/tmp/bad",
    )
    decision = store.record_implementation_proof(
        "impl-bad",
        implementation_files=["src/change.py"],
        api_endpoints=[],
        tests={"passed": True},
        local_commit_sha="aaa",
        pushed_branch_sha="bbb",
        pr_number=43,
        pr_url="https://github.com/example/repo/pull/43",
        pr_head_sha="bbb",
    )
    assert not decision.eligible_for_review
    row = store.get_implementation_execution("impl-bad")
    assert row is not None
    assert row["state"] == "NEEDS_REWORK"
    assert row["proof_matched"] == 0
