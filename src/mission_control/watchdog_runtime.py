from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .scheduler import MissionScheduler, WorkerSlot
from .store import MissionStore


def build_scheduler(
    *,
    db: Path,
    runtime_root: Path,
    worktree_root: Path,
    max_parallel: int,
) -> MissionScheduler:
    store = MissionStore(db)
    store.initialize()
    adapter_runtime = runtime_root / "agents"
    adapters: dict[str, object] = {}
    availability: dict[str, bool] = {"codex": False, "claude": False}

    for provider, adapter_type in (
        ("codex", CodexAdapter),
        ("claude", ClaudeAdapter),
    ):
        try:
            adapter = adapter_type(
                store,
                runtime_root=adapter_runtime / provider,
            )
            auth = adapter.auth_status()
            adapters[provider] = adapter
            availability[provider] = bool(auth.get("authenticated"))
        except (FileNotFoundError, OSError):
            availability[provider] = False

    workers = (
        WorkerSlot("codex-01", "codex", enabled=availability["codex"]),
        WorkerSlot("claude-01", "claude", enabled=availability["claude"]),
        WorkerSlot("codex-02", "codex", enabled=availability["codex"]),
    )
    return MissionScheduler(
        store,
        workers=workers,
        adapters=adapters,
        worktree_root=worktree_root,
        max_parallel_writers=max_parallel,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=".runtime/mission-control.db")
    parser.add_argument("--runtime-root", default=".runtime/watchdog")
    parser.add_argument(
        "--worktree-root",
        default=r"C:\Users\agent\Documents\GitHub\.worktrees",
    )
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    scheduler = build_scheduler(
        db=Path(args.db).resolve(),
        runtime_root=Path(args.runtime_root).resolve(),
        worktree_root=Path(args.worktree_root),
        max_parallel=args.max_parallel,
    )
    while True:
        snapshot = scheduler.tick()
        print(json.dumps(snapshot, default=lambda value: value.__dict__, sort_keys=True))
        if args.once:
            return 0
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
