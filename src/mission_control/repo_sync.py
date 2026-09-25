"""Read-only canonical repo-sync and publication-auth readiness.

The inspector never mutates a repository: every git invocation passes through a
subcommand allowlist and fails closed if anything else is requested. GitHub
authentication state is evaluated separately from self-hosted (codestra-local)
publication so an unavailable ``gh`` login never blocks local delivery.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from .git_executor import GitWorktreeExecutor

PROTECTED_BRANCHES = frozenset({"main", "master", "staging", "production"})
SELF_HOSTED_REMOTE = "codestra-local"

# Only these git subcommands may run. Anything else is a programming error and
# raises before a process is spawned.
READ_ONLY_GIT = frozenset(
    {
        "rev-parse",
        "branch",
        "status",
        "remote",
        "rev-list",
        "ls-remote",
    }
)
READ_ONLY_GH = (("auth", "status"),)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class RepoSyncError(RuntimeError):
    pass


class MutatingCommandRefused(RepoSyncError):
    pass


class AuthState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    CLI_MISSING = "CLI_MISSING"


class RemoteKind(StrEnum):
    SELF_HOSTED = "SELF_HOSTED"
    GITHUB = "GITHUB"
    OTHER = "OTHER"


class Readiness(StrEnum):
    READY = "READY"
    UP_TO_DATE = "UP_TO_DATE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class GitHubAuth:
    state: AuthState
    detail: str
    account: str | None = None


@dataclass(frozen=True)
class RemoteSync:
    name: str
    url: str
    kind: RemoteKind
    reachable: bool | None
    tracking_ref: str | None
    remote_sha: str | None
    live_sha: str | None
    ahead: int | None
    behind: int | None
    pending_push: bool
    diverged: bool
    stale_tracking_ref: bool


@dataclass(frozen=True)
class RepoSyncStatus:
    path: str
    branch: str | None
    detached: bool
    head_sha: str
    dirty: bool
    staged_count: int
    unstaged_count: int
    untracked_count: int
    local_only: bool
    pending_push: bool
    remotes: tuple[RemoteSync, ...]

    def remote(self, name: str) -> RemoteSync | None:
        return next((item for item in self.remotes if item.name == name), None)


@dataclass(frozen=True)
class PublishDecision:
    remote: str
    remote_kind: str | None
    readiness: Readiness
    reasons: tuple[str, ...]
    head_sha: str
    remote_sha: str | None
    branch: str | None
    github_auth: str | None = None


@dataclass(frozen=True)
class SyncReport:
    status: RepoSyncStatus
    github_auth: GitHubAuth
    decisions: tuple[PublishDecision, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["self_hosted_publication"] = _summary(self.decisions, RemoteKind.SELF_HOSTED)
        payload["github_publication"] = _summary(self.decisions, RemoteKind.GITHUB)
        return payload


def _summary(decisions: Sequence[PublishDecision], kind: RemoteKind) -> str:
    states = [d.readiness for d in decisions if d.remote_kind == kind]
    if not states:
        return "NOT_CONFIGURED"
    if Readiness.BLOCKED in states:
        return Readiness.BLOCKED.value
    if Readiness.READY in states:
        return Readiness.READY.value
    return Readiness.UP_TO_DATE.value


def classify_remote(name: str, url: str) -> RemoteKind:
    lowered = url.lower()
    if "github.com" in lowered:
        return RemoteKind.GITHUB
    if (
        name == SELF_HOSTED_REMOTE
        or lowered.startswith(("file://", "/"))
        or (len(url) > 2 and url[1] == ":" and url[2] in "\\/")
    ):
        return RemoteKind.SELF_HOSTED
    return RemoteKind.OTHER


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"},
    )


class RepoSyncInspector:
    def __init__(
        self,
        *,
        git_executable: str | None = None,
        gh_executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.git = git_executable or GitWorktreeExecutor._discover_git()
        self.gh = gh_executable if gh_executable is not None else shutil.which("gh")
        self.runner = runner or _default_runner

    # -- command guards -------------------------------------------------
    def _git(
        self, repo: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if not args or args[0] not in READ_ONLY_GIT:
            raise MutatingCommandRefused(f"git {' '.join(args)} is not read-only")
        if args[0] == "remote" and args[1:2] not in ((), ("-v",), ("get-url",)):
            raise MutatingCommandRefused(f"git {' '.join(args)} is not read-only")
        if args[0] == "branch" and args[1:] != ("--show-current",):
            raise MutatingCommandRefused(f"git {' '.join(args)} is not read-only")
        try:
            proc = self.runner(
                [self.git, "-c", f"safe.directory={repo}", "-C", str(repo), *args]
            )
        except subprocess.TimeoutExpired as exc:
            if check:
                raise RepoSyncError(f"git {' '.join(args)} timed out") from exc
            return subprocess.CompletedProcess(exc.cmd, 124, "", "timeout")
        if check and proc.returncode != 0:
            raise RepoSyncError(
                f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    # -- GitHub auth ----------------------------------------------------
    def github_auth(self, hostname: str = "github.com") -> GitHubAuth:
        if not self.gh:
            return GitHubAuth(AuthState.CLI_MISSING, "gh CLI not found on PATH")
        argv = [self.gh, *READ_ONLY_GH[0], "--hostname", hostname]
        try:
            proc = self.runner(argv)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return GitHubAuth(AuthState.UNAVAILABLE, f"gh auth status failed: {exc}")
        text = f"{proc.stdout}\n{proc.stderr}"
        if proc.returncode != 0:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            return GitHubAuth(AuthState.UNAVAILABLE, first or "gh auth status non-zero")
        account = None
        for line in text.splitlines():
            marker = " account "
            if marker in line:
                account = line.split(marker, 1)[1].split()[0]
                break
        return GitHubAuth(AuthState.AVAILABLE, "gh auth status ok", account)

    # -- repository state -----------------------------------------------
    def status(self, repo: str | Path, *, live: bool = False) -> RepoSyncStatus:
        path = Path(repo).resolve()
        if not (path / ".git").exists():
            raise RepoSyncError(f"not a Git checkout: {path}")

        head = self._git(path, "rev-parse", "HEAD").stdout.strip()
        branch = self._git(path, "branch", "--show-current").stdout.strip() or None
        porcelain = self._git(path, "status", "--porcelain=v1").stdout.splitlines()
        staged = unstaged = untracked = 0
        for line in porcelain:
            if line.startswith("??"):
                untracked += 1
                continue
            if line[:1] not in (" ", "?"):
                staged += 1
            if line[1:2] not in (" ", ""):
                unstaged += 1

        remotes: list[RemoteSync] = []
        names = self._git(path, "remote").stdout.split()
        for name in sorted(names):
            url = self._git(path, "remote", "get-url", name).stdout.strip()
            remotes.append(self._remote_sync(path, name, url, branch, head, live))

        pending = any(r.pending_push for r in remotes)
        local_only = branch is not None and all(r.remote_sha is None for r in remotes)
        return RepoSyncStatus(
            path=str(path),
            branch=branch,
            detached=branch is None,
            head_sha=head,
            dirty=bool(porcelain),
            staged_count=staged,
            unstaged_count=unstaged,
            untracked_count=untracked,
            local_only=local_only,
            pending_push=pending or local_only,
            remotes=tuple(remotes),
        )

    def _remote_sync(
        self,
        path: Path,
        name: str,
        url: str,
        branch: str | None,
        head: str,
        live: bool,
    ) -> RemoteSync:
        kind = classify_remote(name, url)
        tracking_ref = f"refs/remotes/{name}/{branch}" if branch else None
        remote_sha = None
        if tracking_ref:
            proc = self._git(
                path, "rev-parse", "--verify", "--quiet", f"{tracking_ref}^{{commit}}",
                check=False,
            )
            if proc.returncode == 0:
                remote_sha = proc.stdout.strip() or None

        reachable: bool | None = None
        live_sha: str | None = None
        if kind == RemoteKind.SELF_HOSTED and not url.lower().startswith("file://"):
            reachable = Path(url).exists()
        if live and branch:
            proc = self._git(
                path, "ls-remote", "--heads", name, f"refs/heads/{branch}", check=False
            )
            reachable = proc.returncode == 0
            if reachable:
                first = proc.stdout.split()
                live_sha = first[0] if first else None

        if live and reachable:
            # ls-remote is authoritative; a missing head means the branch is absent.
            effective_sha = live_sha
            stale = live_sha != remote_sha
        else:
            effective_sha = remote_sha
            stale = False
        ahead = behind = None
        if effective_sha:
            known = self._git(
                path, "rev-parse", "--verify", "--quiet", f"{effective_sha}^{{commit}}",
                check=False,
            )
            if known.returncode == 0:
                counts = self._git(
                    path, "rev-list", "--left-right", "--count", f"{effective_sha}...{head}"
                ).stdout.split()
                if len(counts) == 2:
                    behind, ahead = map(int, counts)
        pending_push = bool(branch) and (effective_sha is None or bool(ahead))
        diverged = bool(ahead) and bool(behind)
        return RemoteSync(
            name=name,
            url=_redact(url),
            kind=kind,
            reachable=reachable,
            tracking_ref=tracking_ref,
            remote_sha=effective_sha,
            live_sha=live_sha,
            ahead=ahead,
            behind=behind,
            pending_push=pending_push,
            diverged=diverged,
            stale_tracking_ref=stale,
        )

    # -- publication readiness ------------------------------------------
    def publish_decision(
        self,
        status: RepoSyncStatus,
        remote: str,
        auth: GitHubAuth | None = None,
    ) -> PublishDecision:
        reasons: list[str] = []
        sync = status.remote(remote)
        if sync is None:
            return PublishDecision(
                remote=remote,
                remote_kind=None,
                readiness=Readiness.BLOCKED,
                reasons=(f"remote_not_configured:{remote}",),
                head_sha=status.head_sha,
                remote_sha=None,
                branch=status.branch,
            )
        if status.detached:
            reasons.append("detached_head")
        if status.branch in PROTECTED_BRANCHES:
            reasons.append(f"protected_branch:{status.branch}")
        if status.dirty:
            reasons.append(
                "dirty_worktree:"
                f"staged={status.staged_count},unstaged={status.unstaged_count},"
                f"untracked={status.untracked_count}"
            )
        if sync.reachable is False:
            reasons.append("remote_unreachable")
        if sync.remote_sha and sync.ahead is None:
            reasons.append("remote_sha_unknown_locally_fetch_required")
        if sync.behind:
            reasons.append(
                "remote_diverged" if sync.diverged else "remote_has_newer_work"
            )
        auth_state = None
        if sync.kind == RemoteKind.GITHUB:
            auth_state = (auth.state if auth else AuthState.UNAVAILABLE).value
            if auth_state != AuthState.AVAILABLE:
                reasons.append(f"github_auth_{auth_state.lower()}")

        if reasons:
            readiness = Readiness.BLOCKED
        elif sync.pending_push:
            readiness = Readiness.READY
        else:
            readiness = Readiness.UP_TO_DATE
        return PublishDecision(
            remote=remote,
            remote_kind=sync.kind.value,
            readiness=readiness,
            reasons=tuple(reasons),
            head_sha=status.head_sha,
            remote_sha=sync.remote_sha,
            branch=status.branch,
            github_auth=auth_state,
        )

    def report(self, repo: str | Path, *, live: bool = False) -> SyncReport:
        status = self.status(repo, live=live)
        needs_auth = any(r.kind == RemoteKind.GITHUB for r in status.remotes)
        auth = (
            self.github_auth()
            if needs_auth
            else GitHubAuth(AuthState.UNAVAILABLE, "no GitHub remote configured")
        )
        decisions = tuple(
            self.publish_decision(status, r.name, auth) for r in status.remotes
        )
        return SyncReport(status=status, github_auth=auth, decisions=decisions)


def _redact(url: str) -> str:
    """Drop embedded credentials (https://user:token@host/...)."""
    if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url
