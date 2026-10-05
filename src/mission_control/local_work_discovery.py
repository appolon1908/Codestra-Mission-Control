from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess


@dataclass(frozen=True)
class LocalLane:
    repository: str
    worktree: str
    branch: str
    head: str
    dirty: int
    ahead: int | None
    behind: int | None
    committed_at: str
    classification: str
    reason: str


class LocalWorkDiscovery:
    """Proof-derived discovery of local Git work not necessarily known by Router/PR state."""

    def __init__(self, repository_root: str | Path | None = None):
        self.root = Path(
            repository_root
            or os.getenv("CODESTRA_REPOSITORY_ROOT", "/home/codestra/Documents/GitHub")
        )

    @staticmethod
    def _git(path: Path, *args: str, check: bool = True) -> str:
        try:
            p = subprocess.run(
                ["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            if check:
                raise RuntimeError("git_timeout")
            return ""
        if check and p.returncode:
            raise RuntimeError(p.stderr.strip() or "git_failed")
        return p.stdout.strip()

    def _repos(self, repository: str | None):
        if repository:
            p = self.root / repository
            return [(repository, p)] if (p / ".git").exists() else []
        return [
            (p.name, p) for p in sorted(self.root.iterdir()) if p.is_dir() and (p / ".git").exists()
        ]

    def repository_names(self) -> list[str]:
        return [name for name, _ in self._repos(None)]

    def scan(self, repository: str | None = None, recent_hours: int = 48) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, min(recent_hours, 720)))
        out = []
        for name, repo in self._repos(repository):
            raw = self._git(repo, "worktree", "list", "--porcelain", check=False)
            blocks = [b for b in raw.split("\n\n") if b.strip()]
            for block in blocks:
                x = {}
                for line in block.splitlines():
                    k, _, v = line.partition(" ")
                    if k in {"worktree", "HEAD", "branch"}:
                        x[k] = v
                wt = Path(x.get("worktree", ""))
                if not wt.exists():
                    continue
                committed = self._git(wt, "show", "-s", "--format=%cI", "HEAD", check=False)
                try:
                    dt = datetime.fromisoformat(committed).astimezone(timezone.utc)
                except Exception:
                    continue
                dirty = len(self._git(wt, "status", "--porcelain", check=False).splitlines())
                branch = self._git(wt, "branch", "--show-current", check=False) or "DETACHED"
                ab = self._git(
                    wt, "rev-list", "--left-right", "--count", "@{upstream}...HEAD", check=False
                ).split()
                behind = ahead = None
                if len(ab) == 2:
                    try:
                        behind, ahead = map(int, ab)
                    except ValueError:
                        pass
                recent = dt >= cutoff
                lower = branch.lower()
                if dirty:
                    cls, reason = (
                        "DIRTY_UNCLASSIFIED",
                        f"{dirty} working-tree entries require preservation/classification",
                    )
                elif ahead and behind:
                    cls, reason = (
                        "NEEDS_RECONCILIATION",
                        f"local/remote diverged: ahead {ahead}, behind {behind}",
                    )
                elif branch == "DETACHED" or lower.startswith(
                    ("preserve/", "archive/", "backup/", "recovery/")
                ):
                    cls, reason = "PRESERVED_EVIDENCE", "clean preservation/evidence lane"
                elif ahead:
                    cls, reason = (
                        "UNPUBLISHED_IMPLEMENTATION",
                        f"clean local lane is {ahead} commit(s) ahead of upstream",
                    )
                elif (
                    ahead is None
                    and recent
                    and lower.startswith(("feature/", "mission/", "impl/", "convergence/", "work/"))
                ):
                    cls, reason = (
                        "ACTIVE_IMPLEMENTATION",
                        "recent clean implementation lane has no upstream tracking authority",
                    )
                elif ahead == 0 and behind == 0 and branch not in {"main", "master"} and recent:
                    cls, reason = (
                        "READY_FOR_CERTIFICATION",
                        "clean tracked implementation lane matches upstream",
                    )
                elif ahead is None and recent:
                    cls, reason = (
                        "UNPUBLISHED_IMPLEMENTATION",
                        "recent clean branch has no upstream tracking authority",
                    )
                else:
                    cls, reason = (
                        "REPRESENTED",
                        "clean lane has no proof of unpublished local commits",
                    )
                if recent or dirty or cls != "REPRESENTED":
                    out.append(
                        asdict(
                            LocalLane(
                                name,
                                str(wt),
                                branch,
                                x.get("HEAD", "")
                                or self._git(wt, "rev-parse", "HEAD", check=False),
                                dirty,
                                ahead,
                                behind,
                                committed,
                                cls,
                                reason,
                            )
                        )
                    )
        return sorted(
            out,
            key=lambda r: (
                r["classification"] == "REPRESENTED",
                r["repository"].lower(),
                r["branch"],
            ),
        )

    def summary(self, repository: str | None = None, recent_hours: int = 48) -> dict:
        lanes = self.scan(repository, recent_hours)
        counts = {}
        for lane in lanes:
            counts[lane["classification"]] = counts.get(lane["classification"], 0) + 1
        return {
            "repository": repository,
            "recent_hours": recent_hours,
            "counts": counts,
            "lanes": lanes,
        }
