#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from mission_control.store import MissionStore


DYNAMIC_DEFAULTS = {
    "current-mission.json": {
        "mission_id": None,
        "status": "IDLE",
        "writer": None,
        "updated_at": None,
    },
    "lease.json": {
        "mission_id": None,
        "agent_id": None,
        "role": None,
        "expires_at": None,
    },
    "checkpoint.json": {
        "mission_id": None,
        "agent_id": None,
        "state": "NONE",
        "head_sha": None,
        "dirty_count": None,
        "tests": {},
        "blockers": [],
    },
    "handoff.json": {
        "mission_id": None,
        "agent_id": None,
        "state": "NONE",
        "head_sha": None,
        "summary": None,
        "tests": [],
        "blockers": [],
        "request_next_task": False,
    },
}


def _git(path: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _ensure_local_exclude(repo: Path) -> None:
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = existing.splitlines()
    if ".codestra-mission/" not in lines:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += ".codestra-mission/\n"
        exclude.write_text(existing, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: dict, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _workspace_name(repo_name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in repo_name)


def bootstrap(
    *,
    portfolio_file: Path,
    local_root: Path,
    hub_root: Path,
    db_path: Path,
) -> dict:
    payload = json.loads(portfolio_file.read_text(encoding="utf-8"))
    repos = payload["repositories"]
    store = MissionStore(db_path)
    store.initialize()

    workspace_dir = hub_root / "Workspaces" / "Portfolio"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    local_count = 0
    onboarded = 0
    missing: list[str] = []

    for item in repos:
        name = item["name"]
        repo = local_root / name
        local_present = (repo / ".git").exists()

        mission_path: Path | None = None
        workspace_path: Path | None = None
        origin = item.get("clone_url")

        if local_present:
            local_count += 1
            _ensure_local_exclude(repo)
            mission_path = repo / ".codestra-mission"
            mission_path.mkdir(parents=True, exist_ok=True)

            branch = _git(repo, "branch", "--show-current")
            head = _git(repo, "rev-parse", "HEAD")
            git_origin = _git(repo, "config", "--get", "remote.origin.url")
            status = _git(repo, "status", "--porcelain=v1")
            dirty_count = len([line for line in status.splitlines() if line.strip()])

            _write_json(
                mission_path / "repository.json",
                {
                    "repository": name,
                    "full_name": item.get("full_name"),
                    "local_path": str(repo),
                    "origin_url": git_origin or origin,
                    "default_branch": item.get("default_branch"),
                    "current_branch": branch or None,
                    "head_sha": head or None,
                    "dirty_count_at_bootstrap": dirty_count,
                    "mission_control_root": str(local_root / "Codestra-Mission-Control"),
                    "authority": {
                        "linear": "live mission/task authority",
                        "github": "code/PR/CI authority",
                        "notion": "architecture/continuity authority",
                        "local": "dirty/unpushed work authority",
                    },
                },
                overwrite=True,
            )
            _write_json(
                mission_path / "policy.json",
                {
                    "max_active_writers_per_mission": 1,
                    "use_git_worktrees_for_parallel_writes": True,
                    "lease_ttl_seconds": 600,
                    "heartbeat_target_seconds": 120,
                    "approval_levels": {
                        "read": 0,
                        "local_write": 1,
                        "branch_push": 2,
                        "merge": 3,
                        "staging_mutation": 4,
                        "production_effect": 5,
                    },
                    "forbidden_without_explicit_approval": [
                        "force_push",
                        "reset_or_clean_unknown_work",
                        "production_database_write",
                        "live_billing",
                        "live_calling",
                        "live_sms_or_email",
                        "production_provider_effect",
                    ],
                },
                overwrite=True,
            )
            for filename, data in DYNAMIC_DEFAULTS.items():
                _write_json(mission_path / filename, data, overwrite=False)

            readme = mission_path / "README.md"
            if not readme.exists():
                readme.write_text(
                    "# Codestra Mission Channel\n\n"
                    "This directory is local-only and excluded through .git/info/exclude.\n"
                    "It is the repository-local runtime channel for Codestra Mission Control.\n\n"
                    "Rules:\n"
                    "- one active WRITER lease per mission;\n"
                    "- parallel writes use Git worktrees;\n"
                    "- preserve dirty/unpushed work;\n"
                    "- handoff captures branch, HEAD, dirty state, tests and blockers;\n"
                    "- reviewers/verifiers do not become writers without lease transfer;\n"
                    "- merge/staging/production follow approval policy.\n",
                    encoding="utf-8",
                    newline="\n",
                )

            workspace_path = workspace_dir / f"{_workspace_name(name)}.code-workspace"
            workspace = {
                "folders": [
                    {"name": name, "path": str(repo)},
                    {
                        "name": "MISSION CONTROL",
                        "path": str(local_root / "Codestra-Mission-Control"),
                    },
                ],
                "settings": {
                    "files.exclude": {
                        "**/.codestra-mission": False,
                        "**/__pycache__": True,
                        "**/.pytest_cache": True,
                    }
                },
            }
            workspace_path.write_text(
                json.dumps(workspace, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            onboarded += 1
        else:
            missing.append(name)

        store.upsert_repository(
            name,
            full_name=item.get("full_name"),
            local_path=str(repo),
            origin_url=origin,
            default_branch=item.get("default_branch"),
            visibility=item.get("visibility"),
            local_present=local_present,
            mission_channel_path=str(mission_path) if mission_path else None,
            workspace_path=str(workspace_path) if workspace_path else None,
        )

    return {
        "portfolio_count": len(repos),
        "local_count": local_count,
        "onboarded_count": onboarded,
        "missing_count": len(missing),
        "missing": sorted(missing, key=str.lower),
        "database": str(db_path),
        "workspace_dir": str(workspace_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", default="config/portfolio.repositories.json")
    parser.add_argument("--local-root", default=r"C:\Users\agent\Documents\GitHub")
    parser.add_argument(
        "--hub-root",
        default=r"C:\Users\agent\Desktop\Codestra-Development-Hub",
    )
    parser.add_argument("--db", default=".runtime/mission-control.db")
    args = parser.parse_args()
    result = bootstrap(
        portfolio_file=Path(args.portfolio).resolve(),
        local_root=Path(args.local_root),
        hub_root=Path(args.hub_root),
        db_path=Path(args.db).resolve(),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
