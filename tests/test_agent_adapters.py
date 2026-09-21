from __future__ import annotations

import json
from pathlib import Path

import pytest

from mission_control.adapters import AgentAssignment
from mission_control.adapters.claude import ClaudeAdapter
from mission_control.adapters.codex import CodexAdapter
from mission_control.lease import LeaseManager, LeaseNotOwned
from mission_control.models import Mission
from mission_control.store import MissionStore


def assignment(worktree: Path) -> AgentAssignment:
    return AgentAssignment(
        mission_id="PAS-184",
        agent_id="codex-01",
        repository="repo",
        worktree=str(worktree),
        branch="mission/pas-184-codex-01",
        base_sha="a" * 40,
        goal="implement bounded change",
        acceptance=("tests pass",),
        include_paths=("src/**",),
        exclude_paths=("src/generated/**",),
    )


def test_common_assignment_contains_file_fence(tmp_path):
    value = assignment(tmp_path)
    assert value.include_paths == ("src/**",)
    assert value.exclude_paths == ("src/generated/**",)
    assert value.max_turns == 30


def test_codex_command_uses_workspace_sandbox(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    adapter = object.__new__(CodexAdapter)
    adapter.executable = "codex"
    command = adapter.build_command(
        assignment(tmp_path),
        "prompt",
        tmp_path,
    )
    assert command[:3] == ["codex", "exec", "--json"]
    assert ["--sandbox", "workspace-write"] == command[3:5]
    assert "--approve-for-me" in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_claude_command_is_restricted_and_noninteractive(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    adapter = object.__new__(ClaudeAdapter)
    adapter.executable = "claude"
    command = adapter.build_command(
        assignment(tmp_path),
        "prompt",
        tmp_path,
    )
    assert "--print" in command
    assert "--restricted" in command
    assert "--permission-prompts" in command
    assert "none" in command
    assert "--dangerously-skip-permissions" not in command
    assert ClaudeAdapter.SAFE_TOOLS in command
    assert "Bash" not in ClaudeAdapter.SAFE_TOOLS


def test_dispatch_requires_writer_lease(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-184", "repo", "goal"))

    adapter = object.__new__(CodexAdapter)
    adapter.name = "codex"
    adapter.store = store
    adapter.leases = LeaseManager(store)

    with pytest.raises(LeaseNotOwned):
        adapter._assert_writer_lease(assignment(repo))


def test_execution_store_round_trip(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-184", "repo", "goal"))
    store.create_agent_execution(
        execution_id="exec-1",
        mission_id="PAS-184",
        agent_id="codex-01",
        provider="codex",
        state="RUNNING",
        runner_pid=123,
        worktree="/worktree",
        command=["codex", "exec"],
        stdout_path="/tmp/stdout",
        stderr_path="/tmp/stderr",
        result_path="/tmp/result",
    )
    row = store.get_agent_execution("exec-1")
    assert row is not None
    assert row["provider"] == "codex"
    assert json.loads(row["command_json"]) == ["codex", "exec"]
    store.update_agent_execution(
        "exec-1",
        state="COMPLETED",
        session_id="thread-1",
        exit_code=0,
    )
    row = store.get_agent_execution("exec-1")
    assert row["state"] == "COMPLETED"
    assert row["session_id"] == "thread-1"
    assert row["exit_code"] == 0
