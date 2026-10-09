from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable


class RepoClassification(StrEnum):
    SYNCED = "SYNCED"
    LOCAL_AHEAD = "LOCAL_AHEAD"
    LOCAL_BEHIND = "LOCAL_BEHIND"
    DIRTY_PRESERVE = "DIRTY_PRESERVE"
    DIVERGED_PRESERVE = "DIVERGED_PRESERVE"
    UNINITIALIZED = "UNINITIALIZED"
    NO_ORIGIN = "NO_ORIGIN"
    WRONG_OWNER = "WRONG_OWNER"
    INSPECTION_FAILED = "INSPECTION_FAILED"


@dataclass(frozen=True)
class RepoSnapshot:
    path: str
    head_sha: str | None
    branch: str | None
    origin_url: str | None
    dirty_count: int
    ahead: int | None
    behind: int | None
    classification: RepoClassification
    reason: str


Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def _origin_owner(url: str) -> str | None:
    text = url.strip()
    match = re.search(r"github\.com[:/]([^/]+)/", text, re.IGNORECASE)
    return match.group(1) if match else None


def classify_values(
    *,
    path: str,
    head_sha: str | None,
    branch: str | None,
    origin_url: str | None,
    dirty_count: int,
    ahead: int | None,
    behind: int | None,
    expected_owner: str = "ingtrader21-spec",
) -> RepoSnapshot:
    if not head_sha:
        return RepoSnapshot(
            path,
            None,
            branch,
            origin_url,
            dirty_count,
            ahead,
            behind,
            RepoClassification.UNINITIALIZED,
            "repository has no resolvable HEAD",
        )

    if not origin_url:
        return RepoSnapshot(
            path,
            head_sha,
            branch,
            None,
            dirty_count,
            ahead,
            behind,
            RepoClassification.NO_ORIGIN,
            "origin remote is missing",
        )

    owner = _origin_owner(origin_url)
    if owner and owner.lower() != expected_owner.lower():
        return RepoSnapshot(
            path,
            head_sha,
            branch,
            origin_url,
            dirty_count,
            ahead,
            behind,
            RepoClassification.WRONG_OWNER,
            f"origin owner is {owner}, expected {expected_owner}",
        )

    if dirty_count:
        category = (
            RepoClassification.DIVERGED_PRESERVE
            if (ahead or 0) > 0 and (behind or 0) > 0
            else RepoClassification.DIRTY_PRESERVE
        )
        return RepoSnapshot(
            path,
            head_sha,
            branch,
            origin_url,
            dirty_count,
            ahead,
            behind,
            category,
            "working tree contains local changes; preserve before reconciliation",
        )

    if (ahead or 0) > 0 and (behind or 0) > 0:
        return RepoSnapshot(
            path,
            head_sha,
            branch,
            origin_url,
            dirty_count,
            ahead,
            behind,
            RepoClassification.DIVERGED_PRESERVE,
            "local and remote histories diverged; no automatic rewrite permitted",
        )
    if (ahead or 0) > 0:
        return RepoSnapshot(
            path,
            head_sha,
            branch,
            origin_url,
            dirty_count,
            ahead,
            behind,
            RepoClassification.LOCAL_AHEAD,
            "committed local work requires reviewed publication",
        )
    if (behind or 0) > 0:
        return RepoSnapshot(
            path,
            head_sha,
            branch,
            origin_url,
            dirty_count,
            ahead,
            behind,
            RepoClassification.LOCAL_BEHIND,
            "local branch is behind its upstream",
        )

    return RepoSnapshot(
        path,
        head_sha,
        branch,
        origin_url,
        dirty_count,
        ahead,
        behind,
        RepoClassification.SYNCED,
        "clean and synchronized",
    )


def inspect_repo(
    path: str | Path,
    *,
    runner: Runner = _run,
    expected_owner: str = "ingtrader21-spec",
) -> RepoSnapshot:
    root = Path(path).resolve()

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return runner(["git", *args], root)

    head = run("rev-parse", "HEAD")
    if head.returncode != 0:
        return classify_values(
            path=str(root),
            head_sha=None,
            branch=None,
            origin_url=None,
            dirty_count=0,
            ahead=None,
            behind=None,
            expected_owner=expected_owner,
        )

    branch_result = run("branch", "--show-current")
    branch = branch_result.stdout.strip() or None
    origin_result = run("remote", "get-url", "origin")
    origin = origin_result.stdout.strip() if origin_result.returncode == 0 else None

    status_result = run("status", "--porcelain")
    if status_result.returncode != 0:
        return RepoSnapshot(
            str(root),
            head.stdout.strip(),
            branch,
            origin,
            0,
            None,
            None,
            RepoClassification.INSPECTION_FAILED,
            "git status failed",
        )
    dirty_count = len([line for line in status_result.stdout.splitlines() if line.strip()])

    counts = run("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    ahead: int | None = None
    behind: int | None = None
    if counts.returncode == 0:
        left, right = counts.stdout.strip().split()
        ahead, behind = int(left), int(right)

    return classify_values(
        path=str(root),
        head_sha=head.stdout.strip(),
        branch=branch,
        origin_url=origin,
        dirty_count=dirty_count,
        ahead=ahead,
        behind=behind,
        expected_owner=expected_owner,
    )
