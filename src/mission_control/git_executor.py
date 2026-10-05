from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitExecutorError(RuntimeError):
    pass


class WorktreeConflict(GitExecutorError):
    pass


class FileFenceViolation(GitExecutorError):
    pass


@dataclass(frozen=True)
class RepositoryState:
    path: str
    branch: str | None
    head_sha: str
    dirty_count: int
    upstream: str | None
    ahead: int | None
    behind: int | None


@dataclass(frozen=True)
class WorktreeAssignment:
    repository: str
    mission_id: str
    agent_id: str
    branch: str
    worktree: str
    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class FileFence:
    include: tuple[str, ...] = ("**",)
    exclude: tuple[str, ...] = ()

    def allows(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").lstrip("./")
        included = any(
            pattern in {"*", "**"} or fnmatch.fnmatch(normalized, pattern)
            for pattern in self.include
        )
        excluded = any(fnmatch.fnmatch(normalized, pattern) for pattern in self.exclude)
        return included and not excluded

    def validate(self, relative_paths: list[str]) -> None:
        rejected = sorted({path for path in relative_paths if not self.allows(path)})
        if rejected:
            raise FileFenceViolation("paths outside mission fence: " + ", ".join(rejected))


class GitWorktreeExecutor:
    def __init__(self, git_executable: str | None = None) -> None:
        self.git = git_executable or self._discover_git()

    @staticmethod
    def _discover_git() -> str:
        discovered = shutil.which("git")
        if discovered:
            return discovered

        candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Git" / "cmd" / "git.exe")
        if program_files:
            candidates.append(Path(program_files) / "Git" / "cmd" / "git.exe")
        if program_files_x86:
            candidates.append(Path(program_files_x86) / "Git" / "cmd" / "git.exe")

        candidates.append(Path(r"C:\Users\agent\AppData\Local\Programs\Git\cmd\git.exe"))

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        raise GitExecutorError("git executable was not found")

    def _run(
        self,
        repo: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [
                self.git,
                "-c",
                f"safe.directory={repo}",
                "-C",
                str(repo),
                *args,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitExecutorError(
                f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def inspect(self, repo: str | Path) -> RepositoryState:
        path = Path(repo).resolve()
        if not (path / ".git").exists():
            raise GitExecutorError(f"not a Git checkout: {path}")

        head = self._run(path, "rev-parse", "HEAD").stdout.strip()
        branch = self._run(path, "branch", "--show-current").stdout.strip() or None
        status = self._run(path, "status", "--porcelain=v1").stdout.splitlines()
        upstream_proc = self._run(
            path,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
            check=False,
        )
        upstream = (
            upstream_proc.stdout.strip()
            if upstream_proc.returncode == 0 and upstream_proc.stdout.strip()
            else None
        )
        ahead = behind = None
        if upstream:
            counts = self._run(
                path,
                "rev-list",
                "--left-right",
                "--count",
                f"{upstream}...HEAD",
            ).stdout.split()
            if len(counts) == 2:
                behind, ahead = map(int, counts)

        return RepositoryState(
            path=str(path),
            branch=branch,
            head_sha=head,
            dirty_count=len(status),
            upstream=upstream,
            ahead=ahead,
            behind=behind,
        )

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
        return slug or "mission"

    def resolve(self, repo: str | Path, ref: str) -> str:
        path = Path(repo).resolve()
        return self._run(path, "rev-parse", f"{ref}^{{commit}}").stdout.strip()

    def worktrees(self, repo: str | Path) -> list[dict[str, str]]:
        path = Path(repo).resolve()
        lines = self._run(path, "worktree", "list", "--porcelain").stdout.splitlines()
        entries: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in lines + [""]:
            if not line:
                if current:
                    entries.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return entries

    def create(
        self,
        repo: str | Path,
        *,
        mission_id: str,
        agent_id: str,
        base_ref: str,
        worktree_root: str | Path,
        branch: str | None = None,
    ) -> WorktreeAssignment:
        primary = Path(repo).resolve()
        state_before = self.inspect(primary)
        base_sha = self.resolve(primary, base_ref)

        branch_name = branch or (f"mission/{self._slug(mission_id)}-{self._slug(agent_id)}")
        target = (
            Path(worktree_root).resolve()
            / f"{primary.name}-{self._slug(mission_id)}-{self._slug(agent_id)}"
        )

        if target.exists() and not (target / ".git").exists():
            raise WorktreeConflict(f"target exists and is not a Git worktree: {target}")

        for entry in self.worktrees(primary):
            existing_path = Path(entry["worktree"]).resolve()
            existing_branch = entry.get("branch", "").removeprefix("refs/heads/")
            if existing_path == target:
                head = self.resolve(target, "HEAD")
                if head != base_sha and existing_branch != branch_name:
                    raise WorktreeConflict(
                        f"worktree {target} already belongs to {existing_branch}@{head}"
                    )
                return WorktreeAssignment(
                    repository=primary.name,
                    mission_id=mission_id,
                    agent_id=agent_id,
                    branch=existing_branch or branch_name,
                    worktree=str(target),
                    base_sha=base_sha,
                    head_sha=head,
                )
            if existing_branch == branch_name and existing_path != target:
                raise WorktreeConflict(
                    f"branch {branch_name} is already checked out at {existing_path}"
                )

        branch_exists = (
            self._run(
                primary,
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch_name}",
                check=False,
            ).returncode
            == 0
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        if branch_exists:
            branch_head = self.resolve(primary, branch_name)
            if branch_head != base_sha:
                raise WorktreeConflict(
                    f"existing branch {branch_name} is at {branch_head}, "
                    f"expected exact base {base_sha}"
                )
            self._run(primary, "worktree", "add", str(target), branch_name)
        else:
            self._run(
                primary,
                "worktree",
                "add",
                "-b",
                branch_name,
                str(target),
                base_sha,
            )

        state_after = self.inspect(primary)
        if (
            state_after.head_sha != state_before.head_sha
            or state_after.branch != state_before.branch
            or state_after.dirty_count != state_before.dirty_count
        ):
            raise GitExecutorError(
                "primary checkout changed while creating worktree; refusing assignment"
            )

        return WorktreeAssignment(
            repository=primary.name,
            mission_id=mission_id,
            agent_id=agent_id,
            branch=branch_name,
            worktree=str(target),
            base_sha=base_sha,
            head_sha=self.resolve(target, "HEAD"),
        )

    def takeover(
        self,
        repo: str | Path,
        *,
        mission_id: str,
        new_agent_id: str,
        checkpoint_head: str,
        worktree_root: str | Path,
    ) -> WorktreeAssignment:
        primary = Path(repo).resolve()
        exact = self.resolve(primary, checkpoint_head)
        if exact != checkpoint_head:
            raise GitExecutorError("checkpoint_head must be an exact commit SHA")
        return self.create(
            primary,
            mission_id=mission_id,
            agent_id=new_agent_id,
            base_ref=checkpoint_head,
            worktree_root=worktree_root,
        )

    def changed_paths(self, worktree: str | Path) -> list[str]:
        path = Path(worktree).resolve()
        output = self._run(path, "status", "--porcelain=v1").stdout.splitlines()
        changed: list[str] = []
        for line in output:
            raw = line[3:]
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            changed.append(raw.strip('"').replace("\\", "/"))
        return sorted(set(changed))

    def validate_fence(
        self,
        worktree: str | Path,
        fence: FileFence,
    ) -> None:
        fence.validate(self.changed_paths(worktree))

    def snapshot(self, repo: str | Path) -> str:
        state = self.inspect(repo)
        return json.dumps(state.__dict__, sort_keys=True)
