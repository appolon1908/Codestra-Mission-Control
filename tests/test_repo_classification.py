from mission_control.repo_classification import RepoClassification, classify_values


def classify(**overrides):
    values = {
        "path": "/repo",
        "head_sha": "a" * 40,
        "branch": "main",
        "origin_url": "https://github.com/ingtrader21-spec/example.git",
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
    assert "expected ingtrader21-spec" in snapshot.reason
