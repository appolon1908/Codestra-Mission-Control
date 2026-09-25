from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from mission_control.checkpoint_dispatch import (
    CheckpointDispatcher,
    DispatchClassification,
    ImplementationProof,
)
from mission_control.dispatch_api import DispatchAPI
from mission_control.models import Mission, MissionStatus
from mission_control.store import MissionStore

HEAD = "a" * 40
PASSING = {"total": 12, "failed": 0}


def _store(tmp_path, *, acceptance=()) -> MissionStore:
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission(
            "PAS-29",
            "Codestra-Mission-Control",
            "Build event-driven IDE agent dispatcher",
            status=MissionStatus.WORKING,
            acceptance=list(acceptance),
        )
    )
    return store


def _proof(**overrides) -> ImplementationProof:
    values = {
        "mission_id": "PAS-29",
        "agent_id": "claude-05",
        "implementation_files": ("src/mission_control/checkpoint_dispatch.py",),
        "api_endpoints": ("GET /platform/v1/dispatcher/state",),
        "tests": PASSING,
        "local_commit_sha": HEAD,
        "pushed_branch_sha": HEAD,
        "pr_head_sha": HEAD,
        "pr_url": "https://github.com/example/repo/pull/11",
        "pr_number": 11,
    }
    values.update(overrides)
    return ImplementationProof(**values)


def _checkpoint(store, **overrides) -> int:
    values = {
        "head_sha": HEAD,
        "dirty_count": 0,
        "tests": PASSING,
        "blockers": [],
        "next_task_requested": True,
    }
    values.update(overrides)
    state = values.pop("state", "IMPLEMENTED")
    return store.record_checkpoint("PAS-29", "claude-05", state, **values)


def test_checkpoint_event_references_checkpoint_id(tmp_path):
    store = _store(tmp_path)
    checkpoint_id = _checkpoint(store)
    event = next(
        e for e in store.events("PAS-29") if e["event_type"] == "CHECKPOINT_RECORDED"
    )
    assert json.loads(event["payload_json"])["checkpoint_id"] == checkpoint_id


def test_proven_checkpoint_queues_successor_and_moves_parent_to_review(tmp_path):
    store = _store(tmp_path)
    dispatcher = CheckpointDispatcher(store)
    dispatcher.record_proof(_proof(next_slice="Wire dispatcher into Temporal signals"))
    checkpoint_id = _checkpoint(store)

    [decision] = dispatcher.run()

    assert decision.classification is DispatchClassification.READY_FOR_NEXT
    assert decision.checkpoint_id == checkpoint_id
    assert decision.successor_mission_id == "PAS-29-S1"
    parent = store.get_mission("PAS-29")
    assert parent["status"] == MissionStatus.IN_REVIEW.value
    successor = store.get_mission("PAS-29-S1")
    assert successor["status"] == MissionStatus.QUEUED.value
    assert successor["base_sha"] == HEAD
    assert successor["goal"] == "Wire dispatcher into Temporal signals"
    [task] = dispatcher.tasks()
    assert task["parent_mission_id"] == "PAS-29"
    assert task["root_mission_id"] == "PAS-29"
    assert task["source_checkpoint_id"] == checkpoint_id


def test_consumption_is_idempotent_and_cursor_advances(tmp_path):
    store = _store(tmp_path)
    dispatcher = CheckpointDispatcher(store)
    dispatcher.record_proof(_proof())
    _checkpoint(store)
    assert len(dispatcher.run()) == 1
    assert dispatcher.run() == []
    # A second proven checkpoint at the same head must not create a duplicate task.
    _checkpoint(store)
    [again] = CheckpointDispatcher(store).run()
    assert again.successor_mission_id == "PAS-29-S1"
    assert len(dispatcher.tasks()) == 1
    state = dispatcher.state()
    assert state["pending_checkpoint_events"] == 0
    assert state["classification_counts"]["READY_FOR_NEXT"] == 2


@pytest.mark.parametrize(
    ("proof_overrides", "checkpoint_overrides", "expected"),
    [
        (None, {}, "no implementation proof recorded"),
        ({"pushed_branch_sha": "b" * 40}, {}, "delivery SHA mismatch"),
        ({"pr_head_sha": None}, {}, "delivery SHA missing: PR head"),
        ({"tests": {"total": 3, "failed": 1}}, {}, "passing test evidence"),
        ({"implementation_files": ()}, {}, "material implementation files"),
        ({"pr_url": None}, {}, "pull request URL"),
        ({}, {"dirty_count": 2}, "worktree is dirty"),
        ({}, {"dirty_count": None}, "dirty_count is required"),
        ({}, {"head_sha": None}, "head_sha is required"),
        ({}, {"tests": {"passed": False}}, "failing tests"),
    ],
)
def test_missing_or_invalid_proof_is_rework(
    tmp_path, proof_overrides, checkpoint_overrides, expected
):
    store = _store(tmp_path)
    dispatcher = CheckpointDispatcher(store)
    if proof_overrides is not None:
        dispatcher.record_proof(_proof(**proof_overrides))
    _checkpoint(store, **checkpoint_overrides)

    [decision] = dispatcher.run()

    assert decision.classification is DispatchClassification.REWORK
    assert any(expected in reason for reason in decision.reasons), decision.reasons
    assert decision.successor_mission_id is None
    assert dispatcher.tasks() == []
    assert store.get_mission("PAS-29")["status"] == MissionStatus.WORKING.value


def test_api_required_mission_needs_endpoint_evidence(tmp_path):
    store = _store(tmp_path, acceptance=["api_required"])
    dispatcher = CheckpointDispatcher(store)
    dispatcher.record_proof(_proof(api_endpoints=()))
    _checkpoint(store)
    [decision] = dispatcher.run()
    assert decision.classification is DispatchClassification.REWORK
    assert "API/endpoint evidence is required for this mission" in decision.reasons

    dispatcher.record_proof(_proof())
    _checkpoint(store)
    [decision] = dispatcher.run()
    assert decision.classification is DispatchClassification.READY_FOR_NEXT
    assert dispatcher.tasks()[0]["api_required"] is True


def test_blockers_classify_blocked_even_with_valid_proof(tmp_path):
    store = _store(tmp_path)
    dispatcher = CheckpointDispatcher(store)
    dispatcher.record_proof(_proof())
    _checkpoint(store, blockers=["GitHub PR checks unavailable"])

    [decision] = dispatcher.run()

    assert decision.classification is DispatchClassification.BLOCKED
    assert decision.reasons == ("GitHub PR checks unavailable",)
    assert store.get_mission("PAS-29")["status"] == MissionStatus.BLOCKED.value
    assert dispatcher.tasks() == []


def test_blocking_state_without_blockers_is_blocked(tmp_path):
    store = _store(tmp_path)
    _checkpoint(store, state="TAKEOVER_BLOCKED_DIRTY", next_task_requested=False)
    [decision] = CheckpointDispatcher(store).run()
    assert decision.classification is DispatchClassification.BLOCKED


def test_progress_checkpoint_continues_without_status_change(tmp_path):
    store = _store(tmp_path)
    _checkpoint(store, next_task_requested=False, head_sha=None)
    [decision] = CheckpointDispatcher(store).run()
    assert decision.classification is DispatchClassification.CONTINUE
    assert store.get_mission("PAS-29")["status"] == MissionStatus.WORKING.value


def test_legacy_event_without_checkpoint_reference_is_blocked(tmp_path):
    store = _store(tmp_path)
    with store.connection() as conn:
        MissionStore._event(conn, "PAS-29", "CHECKPOINT_RECORDED", "old", {"state": "X"})
    [decision] = CheckpointDispatcher(store).run()
    assert decision.classification is DispatchClassification.BLOCKED
    assert decision.checkpoint_id is None
    assert store.get_mission("PAS-29")["status"] == MissionStatus.WORKING.value


def test_successor_chain_uses_root_sequence_and_skips_taken_ids(tmp_path):
    store = _store(tmp_path)
    store.upsert_mission(Mission("PAS-29-S1", "Codestra-Mission-Control", "manual"))
    dispatcher = CheckpointDispatcher(store)
    dispatcher.record_proof(_proof())
    _checkpoint(store)
    [first] = dispatcher.run()
    assert first.successor_mission_id == "PAS-29-S2"

    head2 = "c" * 40
    store.set_status("PAS-29-S2", MissionStatus.WORKING)
    dispatcher.record_proof(
        _proof(
            mission_id="PAS-29-S2",
            local_commit_sha=head2,
            pushed_branch_sha=head2,
            pr_head_sha=head2,
        )
    )
    store.record_checkpoint(
        "PAS-29-S2",
        "codex-01",
        "IMPLEMENTED",
        head_sha=head2,
        dirty_count=0,
        tests=PASSING,
        blockers=[],
        next_task_requested=True,
    )
    [second] = dispatcher.run()
    assert second.successor_mission_id == "PAS-29-S3"
    chained = {task["mission_id"]: task for task in dispatcher.tasks()}
    assert chained["PAS-29-S3"]["root_mission_id"] == "PAS-29"
    assert chained["PAS-29-S3"]["parent_mission_id"] == "PAS-29-S2"


def test_record_proof_rejects_unknown_mission(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        CheckpointDispatcher(store).record_proof(_proof(mission_id="NOPE"))


@pytest.fixture
def api(tmp_path):
    store = _store(tmp_path)
    server = DispatchAPI(CheckpointDispatcher(store)).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield store, base
    finally:
        server.shutdown()
        server.server_close()


def _call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_api_proof_run_and_state_roundtrip(api):
    store, base = api
    status, body = _call(base, "GET", "/health")
    assert status == 200 and body["ok"] is True

    status, body = _call(
        base,
        "POST",
        "/platform/v1/dispatcher/proofs",
        {
            "mission_id": "PAS-29",
            "agent_id": "claude-05",
            "implementation_files": ["src/mission_control/checkpoint_dispatch.py"],
            "api_endpoints": ["GET /platform/v1/dispatcher/state"],
            "tests": PASSING,
            "local_commit_sha": HEAD,
            "pushed_branch_sha": HEAD,
            "pr_head_sha": HEAD,
            "pr_url": "https://github.com/example/repo/pull/11",
            "pr_number": 11,
        },
    )
    assert status == 201 and body["proof_id"] == 1

    _checkpoint(store)
    status, body = _call(base, "GET", "/platform/v1/dispatcher/state")
    assert status == 200 and body["pending_checkpoint_events"] == 1

    status, body = _call(base, "POST", "/platform/v1/dispatcher/run", {"limit": 10})
    assert status == 200
    assert body["processed"] == 1
    assert body["decisions"][0]["classification"] == "READY_FOR_NEXT"
    assert body["state"]["pending_checkpoint_events"] == 0
    assert body["state"]["queued_tasks"][0]["mission_id"] == "PAS-29-S1"

    status, body = _call(base, "GET", "/platform/v1/dispatcher/decisions?mission_id=PAS-29")
    assert status == 200 and body["count"] == 1
    assert body["items"][0]["reasons"]

    status, body = _call(base, "GET", "/platform/v1/dispatcher/tasks?status=QUEUED")
    assert status == 200 and body["items"][0]["base_sha"] == HEAD

    status, body = _call(base, "GET", "/platform/v1/dispatcher/proofs?mission_id=PAS-29")
    assert status == 200 and body["items"][0]["implementation_files"]


def test_api_validation_errors(api):
    _, base = api
    assert _call(base, "POST", "/platform/v1/dispatcher/proofs", {"mission_id": "PAS-29"})[0] == 400
    assert (
        _call(
            base,
            "POST",
            "/platform/v1/dispatcher/proofs",
            {"mission_id": "NOPE", "agent_id": "x"},
        )[0]
        == 404
    )
    assert _call(base, "POST", "/platform/v1/dispatcher/run", {"limit": 0})[0] == 400
    assert _call(base, "POST", "/platform/v1/dispatcher/run", {"limit": "5"})[0] == 400
    assert _call(base, "GET", "/platform/v1/dispatcher/decisions?limit=abc")[0] == 400
    assert _call(base, "GET", "/platform/v1/dispatcher/proofs")[0] == 400
    assert _call(base, "GET", "/platform/v1/dispatcher/unknown")[0] == 404
