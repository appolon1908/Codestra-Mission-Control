from __future__ import annotations

import json
import subprocess

import pytest

from mission_control.github_merge import GitHubMergeExecutor, MergeExecutionError
from mission_control.merge_coordinator import MergeCandidate, MergeCoordinator
from mission_control.models import MergeQueueState, Mission, MissionStatus
from mission_control.store import MissionStore

HEAD = "a" * 40
BASE = "b" * 40
TARGET = "c" * 40
MERGE_SHA = "d" * 40


def ready_store(tmp_path) -> MissionStore:
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission(
            "PAS-258",
            "repo",
            "goal",
            head_sha=HEAD,
            base_sha=BASE,
        )
    )
    coordinator = MergeCoordinator(store)
    coordinator.record_review_acceptance(
        "PAS-258",
        actor="reviewer",
        head_sha=HEAD,
    )
    coordinator.record_verification_acceptance(
        "PAS-258",
        actor="verifier",
        head_sha=HEAD,
    )
    result = coordinator.evaluate(
        MergeCandidate(
            mission_id="PAS-258",
            repository="owner/repo",
            pr_number=42,
            head_sha=HEAD,
            base_sha=BASE,
            target_sha=TARGET,
            mergeable=True,
            ci_green=True,
            protected_rules_allow=True,
            base_current=True,
            control_sync_current=True,
        )
    )
    assert result.allowed
    return store


def test_merge_executor_uses_exact_head_compare_and_swap(tmp_path):
    store = ready_store(tmp_path)
    seen = {}

    def runner(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"merged": True, "sha": MERGE_SHA, "message": "ok"}),
            stderr="",
        )

    result = GitHubMergeExecutor(
        store,
        executable="gh",
        runner=runner,
    ).merge("PAS-258")

    assert f"sha={HEAD}" in seen["args"]
    assert "merge_method=squash" in seen["args"]
    assert result.merge_sha == MERGE_SHA
    assert store.get_merge_queue_item("PAS-258")["state"] == MergeQueueState.MERGED.value
    assert store.get_mission("PAS-258")["status"] == MissionStatus.MERGED.value


def test_merge_executor_refuses_non_ready_queue(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-258", "repo", "goal", head_sha=HEAD))
    store.enqueue_merge(
        "PAS-258",
        repository="owner/repo",
        pr_number=42,
        head_sha=HEAD,
        base_sha=BASE,
        target_sha=TARGET,
        state=MergeQueueState.WAITING_REVIEW,
    )

    with pytest.raises(MergeExecutionError, match="not READY"):
        GitHubMergeExecutor(store, executable="gh").merge("PAS-258")


def test_merge_executor_records_github_rejection(tmp_path):
    store = ready_store(tmp_path)

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout=json.dumps({"merged": False, "message": "head moved"}),
            stderr="",
        )

    with pytest.raises(MergeExecutionError, match="head moved"):
        GitHubMergeExecutor(store, executable="gh", runner=runner).merge("PAS-258")

    queue = store.get_merge_queue_item("PAS-258")
    assert queue["state"] == MergeQueueState.FAILED.value
    assert queue["reason"] == "head moved"
