#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REQUIRED_CHANNEL_FILES = (
    "README.md",
    "repository.json",
    "policy.json",
    "current-mission.json",
    "lease.json",
    "checkpoint.json",
    "handoff.json",
)


def _git(path: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def certify(portfolio: Path, local_root: Path, workspace_dir: Path) -> dict:
    config = json.loads(portfolio.read_text(encoding="utf-8"))
    repos = config["repositories"]

    rows = []
    for item in repos:
        name = item["name"]
        repo = local_root / name
        channel = repo / ".codestra-mission"
        exclude = repo / ".git" / "info" / "exclude"
        workspace = workspace_dir / f"{name}.code-workspace"

        local = (repo / ".git").exists()
        missing_channel_files = [
            filename
            for filename in REQUIRED_CHANNEL_FILES
            if not (channel / filename).is_file()
        ] if local else list(REQUIRED_CHANNEL_FILES)

        exclude_ok = False
        leak_lines: list[str] = []
        if local:
            exclude_text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            exclude_ok = ".codestra-mission/" in exclude_text.splitlines()
            status = _git(repo, "status", "--porcelain=v1")
            leak_lines = [
                line for line in status.splitlines()
                if ".codestra-mission" in line
            ]

        rows.append(
            {
                "repository": name,
                "local": local,
                "channel": channel.is_dir(),
                "missing_channel_files": missing_channel_files,
                "exclude_ok": exclude_ok,
                "workspace": workspace.is_file(),
                "mission_git_leaks": leak_lines,
            }
        )

    failures = [
        row
        for row in rows
        if not (
            row["local"]
            and row["channel"]
            and not row["missing_channel_files"]
            and row["exclude_ok"]
            and row["workspace"]
            and not row["mission_git_leaks"]
        )
    ]

    return {
        "portfolio_count": len(rows),
        "local_count": sum(1 for row in rows if row["local"]),
        "channel_count": sum(1 for row in rows if row["channel"]),
        "workspace_count": sum(1 for row in rows if row["workspace"]),
        "git_leak_count": sum(len(row["mission_git_leaks"]) for row in rows),
        "failure_count": len(failures),
        "failures": failures,
        "certified": len(failures) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", default="config/portfolio.repositories.json")
    parser.add_argument("--local-root", default=r"C:\Users\agent\Documents\GitHub")
    parser.add_argument(
        "--workspace-dir",
        default=r"C:\Users\agent\Desktop\Codestra-Development-Hub\Workspaces\Portfolio",
    )
    args = parser.parse_args()

    result = certify(
        Path(args.portfolio).resolve(),
        Path(args.local_root),
        Path(args.workspace_dir),
    )
    print(json.dumps(result, indent=2))
    return 0 if result["certified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
