from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from mission_control.implementation_api import ImplementationAPI
from mission_control.models import Mission
from mission_control.store import MissionStore


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def make_api(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission("PAS-249", "Codestra-Mission-Control", "implement"))
    server = ImplementationAPI(store).server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return store, server, f"http://{host}:{port}"


def test_start_list_and_get_execution(tmp_path):
    _, server, base = make_api(tmp_path)
    status, body = post_json(
        base + "/platform/v1/agent-executions",
        {
            "execution_id": "impl-test-1",
            "mission_id": "PAS-249",
            "agent_id": "codex-builder-01",
            "workstation": "codestra-desktop",
            "provider": "codex",
            "branch": "mission/test",
            "worktree": "/tmp/wt",
            "api_required": True,
        },
    )
    assert status == 201
    assert body["agent_number"] == 1

    with urllib.request.urlopen(
        base + "/platform/v1/agent-executions/impl-test-1", timeout=3
    ) as response:
        detail = json.load(response)
    assert detail["agent_number"] == 1
    assert detail["api_required"] is True
    assert detail["state"] == "STARTED"

    with urllib.request.urlopen(base + "/platform/v1/agent-executions", timeout=3) as response:
        listing = json.load(response)
    assert listing["count"] == 1
    server.shutdown()


def test_proof_rejects_sha_mismatch_then_accepts_exact_match(tmp_path):
    _, server, base = make_api(tmp_path)
    status, _ = post_json(
        base + "/platform/v1/agent-executions",
        {
            "execution_id": "impl-test-2",
            "mission_id": "PAS-249",
            "agent_id": "claude-builder-02",
            "workstation": "codestra-desktop",
            "provider": "claude",
            "branch": "mission/test",
            "worktree": "/tmp/wt2",
            "api_required": True,
        },
    )
    assert status == 201

    proof = {
        "implementation_files": ["src/api.py", "tests/test_api.py"],
        "api_endpoints": ["POST /platform/v1/agent-executions"],
        "tests": {"passed": True},
        "local_commit_sha": "abc",
        "pushed_branch_sha": "def",
        "pr_head_sha": "def",
        "pr_number": 7,
        "pr_url": "https://github.com/example/repo/pull/7",
    }
    status, mismatch = post_json(
        base + "/platform/v1/agent-executions/impl-test-2/proof",
        proof,
    )
    assert status == 200
    assert mismatch["state"] == "NEEDS_REWORK"
    assert mismatch["push_proven"] is False

    proof["pushed_branch_sha"] = "abc"
    proof["pr_head_sha"] = "abc"
    status, matched = post_json(
        base + "/platform/v1/agent-executions/impl-test-2/proof",
        proof,
    )
    assert status == 200
    assert matched["state"] == "PROVEN"
    assert matched["push_proven"] is True
    assert matched["eligible_for_review"] is True
    server.shutdown()
