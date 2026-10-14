import subprocess

import pytest

from mission_control.repo_classification import RepoClassification, classify_values, inspect_repo


def classify(**overrides):
    values = {
        "path": "/repo",
        "head_sha": "a" * 40,
        "branch": "main",
        "origin_url": "https://github.com/appolon1908/example.git",
        "dirty_count": 0,
        "ahead": 0,
        "behind": 0,
    }
    values.update(overrides)
    return classify_values(**values)


def test_clean_synced_repo_is_safe():
    assert classify().classification is RepoClassification.SYNCED


def test_dirty_repo_is_preserved():
    snapshot = classify(dirty_count=3, ahead=1)
    assert snapshot.classification is RepoClassification.DIRTY_PRESERVE
    assert "preserve" in snapshot.reason


def test_diverged_repo_is_never_auto_rewritten():
    snapshot = classify(ahead=2, behind=4)
    assert snapshot.classification is RepoClassification.DIVERGED_PRESERVE
    assert "no automatic rewrite" in snapshot.reason


def test_local_ahead_and_behind_are_distinct():
    assert classify(ahead=2).classification is RepoClassification.LOCAL_AHEAD
    assert classify(behind=2).classification is RepoClassification.LOCAL_BEHIND


def test_uninitialized_and_no_origin_are_explicit():
    assert classify(head_sha=None).classification is RepoClassification.UNINITIALIZED
    assert classify(origin_url=None).classification is RepoClassification.NO_ORIGIN


def test_wrong_github_owner_fails_closed():
    snapshot = classify(origin_url="git@github.com:someone-else/example.git")
    assert snapshot.classification is RepoClassification.WRONG_OWNER
    assert "expected appolon1908" in snapshot.reason


@pytest.mark.parametrize("ahead,behind", [(None, None), (0, None), (None, 0), (-1, 0)])
def test_unknown_or_invalid_comparison_is_not_synced(ahead, behind):
    assert classify(ahead=ahead, behind=behind).classification is RepoClassification.INSPECTION_FAILED


def test_dirty_work_is_preserved_even_without_upstream_comparison():
    assert classify(dirty_count=1, ahead=None, behind=None).classification is RepoClassification.DIRTY_PRESERVE


def test_missing_upstream_is_not_synced(tmp_path):
    for args in [
        ["init", "-b", "main"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "base"],
        ["remote", "add", "origin", "https://github.com/appolon1908/example.git"],
    ]:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    snapshot = inspect_repo(tmp_path)
    assert snapshot.classification is RepoClassification.INSPECTION_FAILED
    assert snapshot.ahead is None and snapshot.behind is None


@pytest.mark.parametrize("error", [FileNotFoundError("git unavailable"), subprocess.TimeoutExpired("git", 10)])
def test_unavailable_git_is_isolated_as_inspection_failure(tmp_path, error):
    def runner(command, cwd):
        raise error

    snapshot = inspect_repo(tmp_path, runner=runner)
    assert snapshot.classification is RepoClassification.INSPECTION_FAILED
