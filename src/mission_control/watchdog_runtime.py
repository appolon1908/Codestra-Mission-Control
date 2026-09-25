from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .scheduler import MissionScheduler, WorkerSlot
from .store import MissionStore
from .watchdog import EscalationPolicy
from .watchdog_api import WatchdogAPI


def build_scheduler(
    *,
    db: Path,
    runtime_root: Path,
    worktree_root: Path,
    max_parallel: int,
    escalation_policy: EscalationPolicy | None = None,
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
        escalation_policy=escalation_policy,
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
    parser.add_argument("--stale-heartbeat-seconds", type=int, default=300)
    parser.add_argument("--escalate-after-seconds", type=int, default=900)
    parser.add_argument("--owner-decision-after-seconds", type=int, default=3600)
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument(
        "--api-port",
        type=int,
        help="serve the watchdog runtime API on this port (disabled when omitted)",
    )
    parser.add_argument(
        "--api-allow-dispatch",
        action="store_true",
        help="allow POST /platform/v1/watchdog/tick to dispatch agents",
    )
    args = parser.parse_args()

    scheduler = build_scheduler(
        db=Path(args.db).resolve(),
        runtime_root=Path(args.runtime_root).resolve(),
        worktree_root=Path(args.worktree_root),
        max_parallel=args.max_parallel,
        escalation_policy=EscalationPolicy(
            stale_heartbeat_seconds=args.stale_heartbeat_seconds,
            escalate_after_seconds=args.escalate_after_seconds,
            owner_decision_after_seconds=args.owner_decision_after_seconds,
        ),
    )
    if args.api_port is not None:
        server = WatchdogAPI(
            scheduler.monitor,
            scheduler=scheduler,
            allow_dispatch=args.api_allow_dispatch,
        ).server(args.api_host, args.api_port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address[:2]
        print(json.dumps({"watchdog_api": f"http://{host}:{port}"}), flush=True)
    while True:
        snapshot = scheduler.tick()
        print(json.dumps(asdict(snapshot), sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    raise SystemExit(main())
