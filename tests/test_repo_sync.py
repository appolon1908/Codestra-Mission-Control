from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mission_control.git_executor import GitWorktreeExecutor
from mission_control.repo_sync import (
    AuthState,
    MutatingCommandRefused,
    Readiness,
    RemoteKind,
    RepoSyncInspector,
    _default_runner,
    _redact,
    classify_remote,
)
from mission_control.repo_sync_api import RepoSyncAPI
from mission_control.store import MissionStore

GIT = GitWorktreeExecutor().git
FAKE_GH = "/nonexistent/fake-gh"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output([GIT, "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def configure(repo: Path) -> None:
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")


def inspector(*, gh_ok: bool | None = True) -> RepoSyncInspector:
    """gh_ok=None simulates a missing gh CLI; False simulates logged-out gh."""

    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        if argv[0] == FAKE_GH:
            if gh_ok:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "",
                    "github.com\n  Logged in to github.com account tester (keyring)\n",
                )
            return subprocess.CompletedProcess(
                argv, 1, "", "You are not logged into any GitHub hosts. Run gh auth login\n"
            )
        return _default_runner(argv)

    result = RepoSyncInspector(gh_executable="" if gh_ok is None else FAKE_GH, runner=runner)
    result.calls = calls  # type: ignore[attr-defined]
    return result


@pytest.fixture()
def topology(tmp_path: Path) -> dict[str, Path]:
    local_remote = tmp_path / "git-remotes" / "Repo.git"
    github_remote = tmp_path / "github.com" / "owner" / "Repo.git"
    for bare in (local_remote, github_remote):
        bare.parent.mkdir(parents=True)
        git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))

    repo = tmp_path / "work"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    configure(repo)
    commit(repo, "README.md", "base\n")
    git(repo, "remote", "add", "codestra-local", str(local_remote))
    git(repo, "remote", "add", "origin", str(github_remote))
    git(repo, "push", "-q", "codestra-local", "main")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "-b", "impl/task")
    return {"repo": repo, "local": local_remote, "github": github_remote, "root": tmp_path}


def decisions(report):
    return {d.remote: d for d in report.decisions}


def test_classify_remote_and_redaction():
    assert classify_remote("origin", "https://github.com/o/r.git") == RemoteKind.GITHUB
    assert classify_remote("origin", "git@github.com:o/r.git") == RemoteKind.GITHUB
    assert classify_remote("codestra-local", "ssh://box/r.git") == RemoteKind.SELF_HOSTED
    assert classify_remote("x", "/srv/git/r.git") == RemoteKind.SELF_HOSTED
    assert classify_remote("x", r"C:\git\r.git") == RemoteKind.SELF_HOSTED
    assert classify_remote("x", "https://gitlab.example/r.git") == RemoteKind.OTHER
    assert _redact("https://user:tok@github.com/o/r.git") == "https://***@github.com/o/r.git"
    assert _redact("git@github.com:o/r.git") == "git@github.com:o/r.git"


def test_local_only_branch_is_pending_push_and_ready(topology):
    head = commit(topology["repo"], "feature.txt", "x\n")
    report = inspector().report(topology["repo"])
    status = report.status
    assert status.head_sha == head
    assert status.local_only and status.pending_push and not status.dirty
    by_remote = decisions(report)
    assert by_remote["codestra-local"].readiness == Readiness.READY
    assert by_remote["origin"].readiness == Readiness.READY
    assert report.to_dict()["self_hosted_publication"] == "READY"


def test_ahead_then_up_to_date_after_publication(topology):
    repo = topology["repo"]
    commit(repo, "a.txt", "a\n")
    git(repo, "push", "-q", "codestra-local", "impl/task")
    head = commit(repo, "b.txt", "b\n")

    status = inspector().status(repo)
    local = status.remote("codestra-local")
    assert (local.ahead, local.behind, local.pending_push) == (1, 0, True)
    assert not status.local_only

    git(repo, "push", "-q", "codestra-local", "impl/task")
    report = inspector().report(repo)
    local_decision = decisions(report)["codestra-local"]
    assert local_decision.readiness == Readiness.UP_TO_DATE
    assert local_decision.remote_sha == head == local_decision.head_sha


def test_github_auth_unavailable_does_not_block_self_hosted(topology):
    commit(topology["repo"], "c.txt", "c\n")
    report = inspector(gh_ok=False).report(topology["repo"])
    assert report.github_auth.state == AuthState.UNAVAILABLE
    by_remote = decisions(report)
    assert by_remote["codestra-local"].readiness == Readiness.READY
    assert by_remote["origin"].readiness == Readiness.BLOCKED
    assert by_remote["origin"].reasons == ("github_auth_unavailable",)
    payload = report.to_dict()
    assert payload["self_hosted_publication"] == "READY"
    assert payload["github_publication"] == "BLOCKED"


def test_gh_cli_missing_is_distinct_state(topology):
    report = inspector(gh_ok=None).report(topology["repo"])
    assert report.github_auth.state == AuthState.CLI_MISSING
    assert decisions(report)["origin"].reasons == ("github_auth_cli_missing",)


def test_dirty_worktree_fails_closed_and_is_untouched(topology):
    repo = topology["repo"]
    head = commit(repo, "d.txt", "d\n")
    (repo / "d.txt").write_text("user edit\n", encoding="utf-8")
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
    (repo / "staged.txt").write_text("s\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    before = git(repo, "status", "--porcelain=v1")

    probe = inspector()
    report = probe.report(repo, live=True)
    status = report.status
    assert (status.staged_count, status.unstaged_count, status.untracked_count) == (1, 1, 1)
    for decision in report.decisions:
        assert decision.readiness == Readiness.BLOCKED
        assert decision.reasons[0].startswith("dirty_worktree:")

    assert git(repo, "status", "--porcelain=v1") == before
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / "d.txt").read_text(encoding="utf-8") == "user edit\n"
    git_subcommands = {
        call[5] for call in probe.calls if call[0] == GIT  # type: ignore[attr-defined]
    }
    assert git_subcommands <= {"rev-parse", "branch", "status", "remote", "rev-list", "ls-remote"}


def test_remote_newer_work_and_divergence_block(topology):
    repo, root = topology["repo"], topology["root"]
    commit(repo, "e.txt", "e\n")
    git(repo, "push", "-q", "codestra-local", "impl/task")

    other = root / "other"
    git(root, "clone", "-q", "-o", "codestra-local", str(topology["local"]), str(other))
    configure(other)
    git(other, "checkout", "-q", "impl/task")
    commit(other, "remote.txt", "newer\n")
    git(other, "push", "-q", "codestra-local", "impl/task")

    # Stale tracking ref: without --live the cached ref still looks up to date.
    cached = inspector().status(repo)
    assert cached.remote("codestra-local").behind == 0

    # Live probe sees the newer remote head but the commit is not fetched locally.
    live_status = inspector().status(repo, live=True)
    local = live_status.remote("codestra-local")
    assert local.stale_tracking_ref and local.reachable
    decision = inspector().publish_decision(live_status, "codestra-local")
    assert decision.readiness == Readiness.BLOCKED
    assert "remote_sha_unknown_locally_fetch_required" in decision.reasons

    git(repo, "fetch", "-q", "codestra-local")
    status = inspector().status(repo, live=True)
    assert status.remote("codestra-local").behind == 1
    decision = inspector().publish_decision(status, "codestra-local")
    assert decision.reasons == ("remote_has_newer_work",)

    commit(repo, "local.txt", "local\n")
    status = inspector().status(repo)
    local = status.remote("codestra-local")
    assert local.diverged and (local.ahead, local.behind) == (1, 1)
    decision = inspector().publish_decision(status, "codestra-local")
    assert decision.reasons == ("remote_diverged",)


def test_protected_branch_detached_and_unknown_remote_block(topology):
    repo = topology["repo"]
    git(repo, "checkout", "-q", "main")
    probe = inspector()
    status = probe.status(repo)
    assert "protected_branch:main" in probe.publish_decision(status, "codestra-local").reasons
    missing = probe.publish_decision(status, "nowhere")
    assert missing.readiness == Readiness.BLOCKED
    assert missing.reasons == ("remote_not_configured:nowhere",)

    git(repo, "checkout", "-q", "--detach")
    status = probe.status(repo)
    assert status.detached and not status.local_only
    assert "detached_head" in probe.publish_decision(status, "codestra-local").reasons


def test_unreachable_self_hosted_remote_blocks(topology):
    repo = topology["repo"]
    commit(repo, "f.txt", "f\n")
    git(repo, "remote", "set-url", "codestra-local", str(topology["root"] / "missing.git"))
    status = inspector().status(repo)
    assert status.remote("codestra-local").reachable is False
    decision = inspector().publish_decision(status, "codestra-local")
    assert decision.reasons == ("remote_unreachable",)


@pytest.mark.parametrize(
    "args",
    [
        ("push", "codestra-local"),
        ("fetch",),
        ("reset", "--hard"),
        ("remote", "remove", "origin"),
        ("branch", "-D", "impl/task"),
        (),
    ],
)
def test_mutating_git_commands_are_refused(topology, args):
    probe = inspector()
    with pytest.raises(MutatingCommandRefused):
        probe._git(topology["repo"], *args)
    assert probe.calls == []  # type: ignore[attr-defined]


def _get(base: str, path: str, method: str = "GET") -> tuple[int, dict]:
    body = b"{}" if method != "GET" else None
    request = urllib.request.Request(base + path, method=method, data=body)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_repo_sync_api_endpoints(topology, tmp_path):
    repo = topology["repo"]
    head = commit(repo, "g.txt", "g\n")
    store = MissionStore(tmp_path / "mc.db")
    store.initialize()
    common = {
        "full_name": "owner/Repo",
        "origin_url": "https://github.com/owner/Repo.git",
        "default_branch": "main",
        "visibility": "private",
        "mission_channel_path": None,
        "workspace_path": None,
    }
    store.upsert_repository("Repo", local_path=str(repo), local_present=True, **common)
    store.upsert_repository(
        "Gone", local_path=str(tmp_path / "gone"), local_present=False, **common
    )

    server = RepoSyncAPI(store, inspector(gh_ok=False)).server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert _get(base, "/health") == (
            200,
            {"ok": True, "service": "mission-control-repo-sync-api"},
        )

        code, auth = _get(base, "/platform/v1/repo-sync/auth")
        assert code == 200
        assert auth["github"]["state"] == "UNAVAILABLE"
        assert auth["self_hosted_requires_github_auth"] is False

        code, report = _get(base, "/platform/v1/repo-sync/repositories/Repo")
        assert code == 200
        assert report["status"]["head_sha"] == head
        assert report["status"]["local_only"] is True
        assert report["self_hosted_publication"] == "READY"
        assert report["github_publication"] == "BLOCKED"

        code, ready = _get(base, "/platform/v1/repo-sync/repositories/Repo/readiness")
        assert (code, ready["remote"], ready["readiness"]) == (200, "codestra-local", "READY")
        code, ready = _get(
            base, "/platform/v1/repo-sync/repositories/Repo/readiness?remote=origin&live=1"
        )
        assert ready["readiness"] == "BLOCKED"
        assert ready["reasons"] == ["github_auth_unavailable"]

        code, listing = _get(base, "/platform/v1/repo-sync/repositories")
        assert code == 200
        assert (listing["count"], listing["errors"], listing["pending_push"]) == (2, 1, 1)

        assert _get(base, "/platform/v1/repo-sync/repositories/Gone")[0] == 409
        assert _get(base, "/platform/v1/repo-sync/repositories/Nope")[0] == 404
        assert _get(base, "/platform/v1/repo-sync/repositories/%2E%2E")[0] == 404
        assert _get(base, "/platform/v1/repo-sync/unknown")[0] == 404
        assert _get(base, "/platform/v1/repo-sync/auth", method="POST")[0] == 405
        assert _get(base, "/platform/v1/repo-sync/repositories/Repo", method="DELETE")[0] == 405
    finally:
        server.shutdown()
        server.server_close()
    assert git(repo, "rev-parse", "HEAD") == head


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "mission_control.cli", "--db", str(tmp_path / "cli.db"), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_publish_readiness_exit_codes(topology, tmp_path):
    repo = topology["repo"]
    commit(repo, "h.txt", "h\n")
    ready = _cli(tmp_path, "publish-readiness", "--repo", str(repo))
    assert ready.returncode == 0, ready.stderr
    assert json.loads(ready.stdout)["readiness"] == "READY"

    (repo / "h.txt").write_text("dirty\n", encoding="utf-8")
    blocked = _cli(tmp_path, "publish-readiness", "--repo", str(repo))
    assert blocked.returncode == 2
    assert json.loads(blocked.stdout)["readiness"] == "BLOCKED"

    status = _cli(tmp_path, "repo-sync-status", "--repo", str(repo))
    assert status.returncode == 0
    assert json.loads(status.stdout)["status"]["dirty"] is True

    missing = _cli(tmp_path, "publish-readiness", "--repo", str(tmp_path / "nope"))
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["ok"] is False
