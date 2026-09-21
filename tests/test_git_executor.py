from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mission_control.git_executor import (
    FileFence,
    FileFenceViolation,
    GitWorktreeExecutor,
)


def git(repo: Path, *args: str) -> str:
    executable = GitWorktreeExecutor().git
    return subprocess.check_output(
        [executable, "-C", str(repo), *args],
        text=True,
    ).strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    executable = GitWorktreeExecutor().git
    subprocess.check_call([executable, "-C", str(path), "init", "-b", "main"])
    subprocess.check_call([executable, "-C", str(path), "config", "user.name", "Test"])
    subprocess.check_call(
        [executable, "-C", str(path), "config", "user.email", "test@example.invalid"]
    )
    (path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.check_call([executable, "-C", str(path), "add", "README.md"])
    subprocess.check_call([executable, "-C", str(path), "commit", "-m", "base"])
    return path


def test_parallel_missions_get_separate_worktrees(repo: Path, tmp_path: Path):
    executor = GitWorktreeExecutor()
    root = tmp_path / "worktrees"
    first = executor.create(
        repo,
        mission_id="PAS-1",
        agent_id="codex",
        base_ref="HEAD",
        worktree_root=root,
    )
    second = executor.create(
        repo,
        mission_id="PAS-2",
        agent_id="claude",
        base_ref="HEAD",
        worktree_root=root,
    )
    assert first.worktree != second.worktree
    assert first.branch != second.branch
    assert first.base_sha == second.base_sha == git(repo, "rev-parse", "HEAD")


def test_primary_dirty_state_is_preserved(repo: Path, tmp_path: Path):
    executor = GitWorktreeExecutor()
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    before = executor.inspect(repo)

    executor.create(
        repo,
        mission_id="PAS-3",
        agent_id="codex",
        base_ref="HEAD",
        worktree_root=tmp_path / "worktrees",
    )

    after = executor.inspect(repo)
    assert after.head_sha == before.head_sha
    assert after.branch == before.branch
    assert after.dirty_count == before.dirty_count == 1
    assert (repo / "README.md").read_text(encoding="utf-8") == "dirty\n"


def test_takeover_uses_exact_checkpoint_sha(repo: Path, tmp_path: Path):
    executor = GitWorktreeExecutor()
    checkpoint = git(repo, "rev-parse", "HEAD")
    assignment = executor.takeover(
        repo,
        mission_id="PAS-4",
        new_agent_id="claude",
        checkpoint_head=checkpoint,
        worktree_root=tmp_path / "worktrees",
    )
    assert assignment.base_sha == checkpoint
    assert git(Path(assignment.worktree), "rev-parse", "HEAD") == checkpoint


def test_file_fence_rejects_out_of_scope_changes(repo: Path, tmp_path: Path):
    executor = GitWorktreeExecutor()
    assignment = executor.create(
        repo,
        mission_id="PAS-5",
        agent_id="codex",
        base_ref="HEAD",
        worktree_root=tmp_path / "worktrees",
    )
    worktree = Path(assignment.worktree)
    (worktree / "src").mkdir()
    (worktree / "src" / "ok.py").write_text("pass\n", encoding="utf-8")
    (worktree / "README.md").write_text("changed\n", encoding="utf-8")

    fence = FileFence(include=("src/**",))
    with pytest.raises(FileFenceViolation):
        executor.validate_fence(worktree, fence)
