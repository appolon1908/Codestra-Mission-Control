import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import BASE, HEAD_A, HEAD_B

from mission_control.lease import LeaseManager
from mission_control.merge_api import PREFIX, MergeCoordinatorAPI
from mission_control.models import Mission
from mission_control.store import MissionStore

MISSION = "PAS-258"


@pytest.fixture
def api(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(Mission(MISSION, "repo", "goal", branch="impl/feature"))
    server = MergeCoordinatorAPI(store).server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield store, f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def snapshot(**overrides):
    payload = {
        "pr_number": 7,
        "head_sha": HEAD_A,
        "head_branch": "impl/feature",
        "base_branch": "main",
        "base_sha": BASE,
        "target_sha": BASE,
        "mergeable": True,
        "required_checks": ["ci"],
        "checks": {"ci": "success"},
        "protected_rules_allow": True,
    }
    payload.update(overrides)
    return payload


def test_full_merge_coordination_flow_over_http(api):
    store, base = api
    mission = f"{PREFIX}/missions/{MISSION}"
    LeaseManager(store).claim(MISSION, "builder-1")

    status, health, headers = call(base, "GET", "/health")
    assert status == 200 and health["ok"] is True
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Mission-Control-API"] == "merge-coordinator-v1"

    status, body, _ = call(base, "GET", f"{mission}/decision")
    assert (status, body["error"]) == (404, "decision_not_found")
    status, body, _ = call(base, "GET", f"{mission}/authorization")
    assert status == 200 and body["authorized"] is False

    status, body, _ = call(
        base, "POST", f"{mission}/head", {"head_sha": HEAD_A, "actor": "builder-1"}
    )
    assert status == 200 and body["head_sha"] == HEAD_A
    store.record_checkpoint(
        MISSION, "builder-1", "PUSHED", head_sha=HEAD_A, dirty_count=0, tests={},
        blockers=[], next_task_requested=False,
    )

    status, body, _ = call(
        base, "POST", f"{mission}/evidence",
        {"role": "reviewer", "head_sha": HEAD_A, "actor": "builder-1", "verdict": "accepted"},
    )
    assert (status, body["error"]) == (409, "evidence_rejected")
    for role, actor in (("REVIEWER", "reviewer-1"), ("VERIFIER", "verifier-1")):
        status, body, _ = call(
            base, "POST", f"{mission}/evidence",
            {"role": role, "head_sha": HEAD_A, "actor": actor, "verdict": "ACCEPTED"},
        )
        assert status == 201, body

    status, blocked, _ = call(base, "POST", f"{mission}/evaluations", snapshot(target_sha="d" * 40))
    assert status == 200
    assert blocked["verdict"] == "BLOCKED" and blocked["next_gate"] == "BUILDER_RESOLVE"
    assert blocked["reasons"] == ["BASE_STALE"]

    status, ready, _ = call(base, "POST", f"{mission}/evaluations", snapshot())
    assert status == 200 and ready["verdict"] == "MERGE_READY" and ready["merge_allowed"] is True
    status, body, _ = call(base, "GET", f"{mission}/authorization")
    assert body["authorized"] is True and body["decision_id"] == ready["decision_id"]
    status, body, _ = call(base, "GET", f"{mission}/decision")
    assert body["decision_id"] == ready["decision_id"] and body["snapshot"]["pr_number"] == 7

    call(base, "POST", f"{mission}/head", {"head_sha": HEAD_B, "actor": "builder-1"})
    status, body, _ = call(base, "GET", f"{mission}/authorization")
    assert body["authorized"] is False
    assert "MERGE_DECISION_STALE_HEAD" in body["reasons"]


def test_http_input_validation(api):
    store, base = api
    mission = f"{PREFIX}/missions/{MISSION}"
    assert call(base, "POST", f"{PREFIX}/missions/NOPE/head",
                {"head_sha": HEAD_A, "actor": "x"})[0] == 404
    assert call(base, "POST", f"{mission}/head", {"head_sha": "short", "actor": "x"})[0] == 400
    assert call(base, "POST", f"{mission}/head", {"head_sha": HEAD_A})[1]["error"] == (
        "missing_fields"
    )
    assert call(base, "POST", f"{mission}/evaluations", {"pr_number": 1})[0] == 400
    assert call(base, "POST", f"{mission}/evaluations", snapshot(mergeable="yes"))[0] == 400
    assert call(base, "POST", f"{mission}/decision", {"x": 1})[0] == 404
    assert call(base, "GET", f"{PREFIX}/missions/NOPE/authorization")[0] == 404
    assert call(base, "GET", "/nope")[0] == 404

    store.upsert_mission(Mission("PAS-250", "repo", "upstream"))
    status, body, _ = call(base, "POST", f"{mission}/dependencies", {"depends_on": "PAS-250"})
    assert status == 201 and body["dependencies"] == ["PAS-250"]
    status, body, _ = call(
        base, "POST", f"{PREFIX}/missions/PAS-250/dependencies", {"depends_on": MISSION}
    )
    assert status == 400 and "cycle" in body["detail"]


def test_http_conflict_classification(api):
    _, base = api
    path = f"{PREFIX}/conflicts/classify"
    status, body, _ = call(
        base, "POST", path, {"mergeable": False, "conflicted_paths": ["README.md", "src/a.py"]}
    )
    assert status == 200
    assert body["label"] == "CLASS-2 SEMANTIC" and body["next_gate"] == "BUILDER_RESOLVE"
    assert [p["label"] for p in body["paths"]] == ["MECHANICAL", "SEMANTIC"]
    status, body, _ = call(base, "POST", path, {"mergeable": None})
    assert body["conflict_class"] == 4
    assert call(base, "POST", path, {"mergeable": "no"})[0] == 400


def _cli(db, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "mission_control.cli", "--db", str(db), *args],
        env=env, text=True, capture_output=True, check=False,
    )


def test_cli_merge_gate_exit_codes(tmp_path):
    db = tmp_path / "mission.db"
    assert _cli(db, "create-mission", "--mission", MISSION, "--repository", "r",
                "--goal", "g").returncode == 0
    assert _cli(db, "claim", "--mission", MISSION, "--agent", "builder-1").returncode == 0

    blocked = _cli(db, "release", "--mission", MISSION, "--agent", "builder-1",
                   "--status", "COMPLETE")
    assert blocked.returncode == 2
    assert json.loads(blocked.stdout)["error"] == "completion_blocked"

    assert _cli(db, "record-head", "--mission", MISSION, "--head-sha", HEAD_A,
                "--actor", "builder-1").returncode == 0
    assert _cli(db, "checkpoint", "--mission", MISSION, "--agent", "builder-1", "--state",
                "PUSHED", "--head-sha", HEAD_A, "--dirty-count", "0").returncode == 0
    self_review = _cli(db, "record-evidence", "--mission", MISSION, "--role", "REVIEWER",
                       "--head-sha", HEAD_A, "--actor", "builder-1", "--verdict", "ACCEPTED")
    assert self_review.returncode == 2
    for role, actor in (("REVIEWER", "reviewer-1"), ("VERIFIER", "verifier-1")):
        assert _cli(db, "record-evidence", "--mission", MISSION, "--role", role, "--head-sha",
                    HEAD_A, "--actor", actor, "--verdict", "ACCEPTED").returncode == 0

    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps(snapshot(checks={"ci": "failure"})))
    failed = _cli(db, "merge-evaluate", "--mission", MISSION, "--snapshot-json", f"@{snap}")
    assert failed.returncode == 3
    assert json.loads(failed.stdout)["next_gate"] == "CI"
    assert _cli(db, "merge-authorization", "--mission", MISSION).returncode == 3
    assert json.loads(
        _cli(db, "policy", "--mission", MISSION, "--action", "merge").stdout
    )["allowed"] is False

    ready = _cli(db, "merge-evaluate", "--mission", MISSION, "--snapshot-json",
                 json.dumps(snapshot()))
    assert ready.returncode == 0, ready.stdout
    assert json.loads(ready.stdout)["verdict"] == "MERGE_READY"
    assert _cli(db, "merge-authorization", "--mission", MISSION).returncode == 0
    assert json.loads(
        _cli(db, "policy", "--mission", MISSION, "--action", "merge").stdout
    )["allowed"] is True

    classify = _cli(db, "classify-conflicts", "--mergeable", "false", "--path",
                    ".github/workflows/ci.yml")
    assert json.loads(classify.stdout)["label"] == "CLASS-4 UNSAFE"
