from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mission_control.git_executor import GitWorktreeExecutor
from mission_control.inventory import (
    ABSENT,
    DRIFT,
    ERROR,
    OK,
    STALE,
    ExpectedHost,
    InventoryScanner,
    InventoryThresholds,
    ProbeError,
    RepositoryTarget,
    latest_report,
    parse_status_v2,
    registry_targets,
)
from mission_control.lease import LeaseManager
from mission_control.models import Mission
from mission_control.store import MissionStore

GIT = GitWorktreeExecutor().git


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            GIT,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


@pytest.fixture()
def origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    commit(seed, "base.txt")
    bare = tmp_path / "origin.git"
    subprocess.check_call([GIT, "clone", "-q", "--bare", str(seed), str(bare)])
    return bare


@pytest.fixture()
def clone(tmp_path: Path, origin: Path) -> Path:
    path = tmp_path / "clone"
    subprocess.check_call([GIT, "clone", "-q", str(origin), str(path)])
    git(path, "fetch", "-q", "origin")
    return path


@pytest.fixture()
def store(tmp_path: Path) -> MissionStore:
    store = MissionStore(tmp_path / "mc.db")
    store.initialize()
    return store


def by_key(report: dict) -> dict[tuple[str, str], dict]:
    return {(item["kind"], item["key"]): item for item in report["items"]}


def worktree_item(report: dict, repo: str, path: Path) -> dict:
    return by_key(report)[("worktree", f"{repo}:{path.resolve()}")]


def test_parse_status_v2_counts_every_drift_dimension():
    state = parse_status_v2(
        "# branch.oid abc123\n"
        "# branch.head feature\n"
        "# branch.upstream origin/feature\n"
        "# branch.ab +3 -2\n"
        "1 .M N... 100644 100644 100644 a b tracked.txt\n"
        "2 R. N... 100644 100644 100644 a b R100 new.txt\told.txt\n"
        "u UU N... 100644 100644 100644 100644 a b c conflict.txt\n"
        "? untracked.txt\n"
        "? other/\n"
    )
    assert state["head_sha"] == "abc123"
    assert state["branch"] == "feature"
    assert (state["ahead"], state["behind"]) == (3, 2)
    assert state["tracked_changes"] == 2
    assert state["conflicted_count"] == 1
    assert state["untracked_count"] == 2
    assert state["upstream_gone"] is False

    gone = parse_status_v2("# branch.oid abc\n# branch.head x\n# branch.upstream origin/x\n")
    assert gone["upstream_gone"] is True
    assert gone["ahead"] is None


def test_clean_synced_checkout_is_ok(clone: Path, store: MissionStore):
    report = InventoryScanner(store=store).scan(repositories=[RepositoryTarget("app", str(clone))])
    assert report["complete"] is True
    repo = by_key(report)[("repository", "app")]
    assert repo["status"] == OK
    assert repo["details"]["remotes"] == ["origin"]
    assert repo["details"]["fetch_age_seconds"] is not None
    item = worktree_item(report, "app", clone)
    assert item["status"] == OK
    assert item["flags"] == []
    assert item["details"]["ahead"] == 0
    assert item["details"]["behind"] == 0
    assert item["details"]["pending_push_commits"] == 0


def test_dirty_untracked_diverged_pending_push(clone: Path, origin: Path, tmp_path: Path):
    other = tmp_path / "other"
    subprocess.check_call([GIT, "clone", "-q", str(origin), str(other)])
    commit(other, "remote-1.txt")
    commit(other, "remote-2.txt")
    git(other, "push", "-q", "origin", "main")
    git(clone, "fetch", "-q", "origin")

    commit(clone, "local.txt")
    (clone / "base.txt").write_text("changed\n", encoding="utf-8")
    (clone / "scratch.txt").write_text("x\n", encoding="utf-8")

    report = InventoryScanner().scan(repositories=[RepositoryTarget("app", str(clone))])
    item = worktree_item(report, "app", clone)
    assert item["status"] == DRIFT
    assert {
        "dirty",
        "untracked",
        "ahead",
        "behind",
        "diverged",
        "pending_push",
        "local_only",
    } <= set(item["flags"])
    details = item["details"]
    assert (details["ahead"], details["behind"]) == (1, 2)
    assert details["tracked_changes"] == 1
    assert details["untracked_count"] == 1
    assert details["dirty_count"] == 2
    assert details["local_only_commits"] == 1
    assert details["pending_push_commits"] == 1


def test_branch_without_upstream_reports_local_only_commits(clone: Path):
    git(clone, "checkout", "-q", "-b", "preservation")
    commit(clone, "a.txt")
    commit(clone, "b.txt")
    item = worktree_item(
        InventoryScanner().scan(repositories=[RepositoryTarget("app", str(clone))]),
        "app",
        clone,
    )
    assert {"no_upstream", "local_only", "pending_push"} <= set(item["flags"])
    assert item["details"]["local_only_commits"] == 2
    assert item["details"]["pending_push_commits"] == 2
    assert item["status"] == DRIFT


def test_fresh_branch_without_upstream_is_informational_only(clone: Path):
    git(clone, "checkout", "-q", "-b", "fresh")
    item = worktree_item(
        InventoryScanner().scan(repositories=[RepositoryTarget("app", str(clone))]),
        "app",
        clone,
    )
    assert item["flags"] == ["no_upstream"]
    assert item["status"] == OK


def test_upstream_gone_and_detached_head(clone: Path, tmp_path: Path):
    git(clone, "checkout", "-q", "-b", "feature")
    commit(clone, "f.txt")
    git(clone, "push", "-q", "-u", "origin", "feature")
    git(clone, "update-ref", "-d", "refs/remotes/origin/feature")

    detached = tmp_path / "detached"
    git(clone, "worktree", "add", "-q", "--detach", str(detached), "main")

    report = InventoryScanner().scan(repositories=[RepositoryTarget("app", str(clone))])
    gone = worktree_item(report, "app", clone)
    assert {"upstream_gone", "local_only", "pending_push"} <= set(gone["flags"])
    assert gone["details"]["upstream"] == "origin/feature"
    assert gone["details"]["ahead"] is None

    head = worktree_item(report, "app", detached)
    assert "detached_head" in head["flags"]
    assert "no_upstream" not in head["flags"]
    assert head["details"]["branch"] is None


def test_missing_worktree_does_not_collapse_sibling_worktrees(clone: Path, tmp_path: Path):
    lost = tmp_path / "lost-worktree"
    git(clone, "worktree", "add", "-q", "-b", "lost", str(lost))
    shutil.rmtree(lost)

    report = InventoryScanner().scan(repositories=[RepositoryTarget("app", str(clone))])
    missing = by_key(report)[("worktree", f"app:{lost}")]
    assert missing["status"] == ERROR
    assert missing["error"]["code"] == "worktree_path_missing"
    assert str(lost) in missing["error"]["message"]
    assert worktree_item(report, "app", clone)["status"] == OK
    assert by_key(report)[("repository", "app")]["details"]["worktrees_not_ok"] == 1
    assert report["complete"] is False


def test_repository_failures_are_isolated_with_exact_errors(clone: Path, tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    report = InventoryScanner().scan(
        repositories=[
            RepositoryTarget("missing", str(tmp_path / "nope")),
            RepositoryTarget("plain", str(plain)),
            RepositoryTarget("uncloned", None),
            RepositoryTarget("app", str(clone)),
        ]
    )
    items = by_key(report)
    assert items[("repository", "missing")]["error"] == {
        "code": "repository_path_missing",
        "message": f"path does not exist: {tmp_path / 'nope'}",
    }
    plain_error = items[("repository", "plain")]["error"]
    assert plain_error["code"] == "not_a_git_repository"
    assert "git rev-parse --git-common-dir exited 128" in plain_error["message"]
    assert "not a git repository" in plain_error["message"].lower()
    assert items[("repository", "uncloned")]["status"] == ABSENT
    assert items[("repository", "app")]["status"] == OK
    assert worktree_item(report, "app", clone)["status"] == OK
    assert report["complete"] is False
    assert {error["key"] for error in report["summary"]["errors"]} == {"missing", "plain"}
    assert report["summary"]["by_status"][ERROR] == 2


def test_unexpected_probe_failure_is_contained(clone: Path, tmp_path: Path):
    class ExplodingProbe:
        def run(self, repo: Path, *args: str) -> str:
            if Path(repo).name == "boom":
                raise ValueError("synthetic failure")
            from mission_control.inventory import GitProbe

            return GitProbe().run(repo, *args)

    boom = tmp_path / "boom"
    boom.mkdir()
    report = InventoryScanner(probe=ExplodingProbe()).scan(
        repositories=[RepositoryTarget("boom", str(boom)), RepositoryTarget("app", str(clone))]
    )
    boom_item = by_key(report)[("repository", "boom")]
    assert boom_item["error"] == {
        "code": "unexpected_error",
        "message": "ValueError: synthetic failure",
    }
    assert worktree_item(report, "app", clone)["status"] == OK


def test_git_timeout_is_reported_exactly(clone: Path):
    class SlowProbe:
        def run(self, repo: Path, *args: str) -> str:
            raise ProbeError("git_timeout", f"git {' '.join(args)} timed out after 1s in {repo}")

    report = InventoryScanner(probe=SlowProbe()).scan(
        repositories=[RepositoryTarget("app", str(clone))]
    )
    error = by_key(report)[("repository", "app")]["error"]
    assert error == {
        "code": "git_timeout",
        "message": f"git rev-parse --git-common-dir timed out after 1s in {clone}",
    }


def test_fetch_staleness_and_never_fetched(clone: Path, tmp_path: Path):
    later = datetime.now(UTC) + timedelta(days=3)
    report = InventoryScanner(clock=lambda: later).scan(
        repositories=[RepositoryTarget("app", str(clone))]
    )
    repo = by_key(report)[("repository", "app")]
    assert repo["flags"] == ["remote_refs_stale"]
    assert repo["status"] == STALE

    (Path(git(clone, "rev-parse", "--absolute-git-dir")) / "FETCH_HEAD").unlink()
    repo = by_key(InventoryScanner().scan(repositories=[RepositoryTarget("app", str(clone))]))[
        ("repository", "app")
    ]
    assert repo["flags"] == ["never_fetched"]
    assert repo["details"]["last_fetch_at"] is None


def test_failed_rescan_keeps_last_verified_instead_of_guessing(clone: Path, store: MissionStore):
    scanner = InventoryScanner(store=store)
    first = scanner.scan(repositories=[RepositoryTarget("app", str(clone))])
    assert first["complete"] is True

    moved = clone.with_name("moved")
    clone.rename(moved)
    second = scanner.scan(repositories=[RepositoryTarget("app", str(clone))])
    failed = by_key(second)[("repository", "app")]
    assert failed["status"] == ERROR
    assert failed["error"]["code"] == "repository_path_missing"
    verified = failed["last_verified"]
    assert verified["authoritative"] is False
    assert verified["scan_id"] == first["scan_id"]
    assert verified["status"] == OK
    assert verified["details"]["path"] == str(clone)

    latest = latest_report(store)
    assert latest["scan_id"] == second["scan_id"]
    assert latest["complete"] is False
    replayed = by_key(latest)[("repository", "app")]
    assert replayed["error"] == failed["error"]
    assert replayed["last_verified"]["scan_id"] == first["scan_id"]


def test_latest_report_marks_old_scans_stale(clone: Path, store: MissionStore):
    InventoryScanner(store=store).scan(repositories=[RepositoryTarget("app", str(clone))])
    fresh = latest_report(store)
    assert fresh["report_stale"] is False
    old = latest_report(
        store,
        thresholds=InventoryThresholds(report_stale_seconds=60),
        now=datetime.now(UTC) + timedelta(minutes=5),
    )
    assert old["report_stale"] is True
    assert old["age_seconds"] >= 299


def tailnet_status(now: datetime) -> dict:
    return {
        "Self": {
            "HostName": "codestra-middleware-core",
            "DNSName": "codestra-middleware-core.tail.ts.net.",
            "TailscaleIPs": ["100.76.208.87"],
            "Online": True,
        },
        "Peer": {
            "a": {
                "HostName": "codestra-desktop",
                "DNSName": "codestra-desktop.tail.ts.net.",
                "TailscaleIPs": ["100.66.139.30"],
                "Online": False,
                "LastSeen": (now - timedelta(hours=2)).isoformat(),
            },
            "b": {
                "HostName": "appolon-laptop",
                "DNSName": "appolon-laptop.tail.ts.net.",
                "TailscaleIPs": ["100.64.209.77"],
                "Online": False,
                "LastSeen": (now - timedelta(minutes=2)).isoformat(),
            },
            "c": {
                "HostName": "rogue",
                "DNSName": "rogue.tail.ts.net.",
                "TailscaleIPs": ["100.1.1.1"],
                "Online": True,
                "LastSeen": "0001-01-01T00:00:00Z",
            },
        },
    }


EXPECTED = [
    ExpectedHost("codestra-middleware-core", "codestra-middleware-core.tail.ts.net."),
    ExpectedHost("codestra-desktop", "CODESTRA-DESKTOP.tail.ts.net"),
    ExpectedHost("appolon-laptop", None),
    ExpectedHost("codestra-vicidial", "codestra-vicidial.tail.ts.net."),
]


def test_host_online_offline_stale_missing_unexpected():
    now = datetime.now(UTC)
    report = InventoryScanner(clock=lambda: now).scan(
        expected_hosts=EXPECTED, status_provider=lambda: tailnet_status(now)
    )
    hosts = by_key(report)
    assert hosts[("host", "codestra-middleware-core")]["status"] == OK
    desktop = hosts[("host", "codestra-desktop")]
    assert desktop["flags"] == ["host_last_seen_stale", "host_offline"]
    assert desktop["details"]["last_seen_age_seconds"] == 7200
    laptop = hosts[("host", "appolon-laptop")]
    assert laptop["flags"] == ["host_offline"]
    assert laptop["status"] == STALE
    assert hosts[("host", "codestra-vicidial")]["flags"] == ["host_missing_from_tailnet"]
    rogue = hosts[("host", "rogue")]
    assert rogue["flags"] == ["host_unexpected"]
    assert rogue["status"] == DRIFT
    assert rogue["details"]["last_seen"] is None
    assert report["complete"] is True


def test_host_source_failure_is_per_host_and_keeps_repositories(clone: Path):
    def broken() -> dict:
        raise FileNotFoundError("tailscale: not installed")

    report = InventoryScanner().scan(
        repositories=[RepositoryTarget("app", str(clone))],
        expected_hosts=EXPECTED[:2],
        status_provider=broken,
    )
    items = by_key(report)
    for host in ("codestra-middleware-core", "codestra-desktop"):
        assert items[("host", host)]["error"] == {
            "code": "host_observation_unavailable",
            "message": "FileNotFoundError: tailscale: not installed",
        }
    assert worktree_item(report, "app", clone)["status"] == OK

    unconfigured = InventoryScanner().scan(expected_hosts=EXPECTED[:1])
    assert unconfigured["items"][0]["error"]["message"] == "no tailnet status source configured"

    invalid = InventoryScanner().scan(expected_hosts=EXPECTED[:1], status_provider=list)
    assert invalid["items"][0]["error"] == {
        "code": "host_observation_invalid",
        "message": "tailnet status must be a JSON object, got list",
    }


def test_runtime_lease_and_execution_staleness(store: MissionStore, tmp_path: Path):
    for mission in ("PAS-1", "PAS-2", "PAS-3"):
        store.upsert_mission(Mission(mission, "repo", "goal"))
    leases = LeaseManager(store)
    leases.claim("PAS-1", "codex-01", ttl_seconds=600)
    leases.claim("PAS-2", "claude-01", ttl_seconds=3600)
    leases.claim("PAS-3", "codex-02", ttl_seconds=60)
    store.create_agent_execution(
        execution_id="exec-1",
        mission_id="PAS-1",
        agent_id="codex-01",
        provider="codex",
        state="RUNNING",
        runner_pid=4242,
        worktree=str(tmp_path),
        command=["codex"],
        stdout_path="o",
        stderr_path="e",
        result_path="r",
    )

    now = datetime.now(UTC)
    fresh = by_key(InventoryScanner(store=store, clock=lambda: now).scan(persist=False))
    assert fresh[("runtime", "lease:PAS-1")]["status"] == OK
    assert fresh[("runtime", "execution:exec-1")]["status"] == OK

    later = now + timedelta(minutes=20)
    items = by_key(InventoryScanner(store=store, clock=lambda: later).scan(persist=False))
    assert items[("runtime", "lease:PAS-1")]["flags"] == ["lease_expired"]
    assert items[("runtime", "lease:PAS-2")]["flags"] == ["heartbeat_stale"]
    assert items[("runtime", "lease:PAS-3")]["flags"] == ["lease_expired"]
    execution = items[("runtime", "execution:exec-1")]
    assert execution["flags"] == ["execution_stale"]
    assert execution["details"]["update_age_seconds"] >= 1200


def test_runtime_store_failure_is_reported(store: MissionStore):
    class BrokenStore(MissionStore):
        def list_leases(self):
            raise sqlite3.OperationalError("database is locked")

    broken = BrokenStore(store.path)
    items = by_key(InventoryScanner(store=broken).scan(persist=False))
    assert items[("runtime", "leases")]["error"] == {
        "code": "runtime_observation_failed",
        "message": "OperationalError: database is locked",
    }


def test_registry_targets_distinguish_absent_and_vanished(store: MissionStore, tmp_path: Path):
    common = {
        "full_name": None,
        "origin_url": None,
        "default_branch": "main",
        "visibility": None,
        "mission_channel_path": None,
        "workspace_path": None,
    }
    store.upsert_repository(
        "never", local_path=str(tmp_path / "never"), local_present=False, **common
    )
    store.upsert_repository(
        "vanished", local_path=str(tmp_path / "gone"), local_present=True, **common
    )
    targets = {target.name: target for target in registry_targets(store)}
    assert targets["never"].path is None
    assert targets["vanished"].path == str(tmp_path / "gone")
