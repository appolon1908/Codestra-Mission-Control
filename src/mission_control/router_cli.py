from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .mission_router import MissionRouter
from .router_api import MissionRouterAPI
from .router_catalog import seed_registered_repositories
from .store import MissionStore


def _router(db: str) -> MissionRouter:
    store = MissionStore(Path(db))
    router = MissionRouter(store)
    router.initialize()
    return router


def main() -> None:
    parser = argparse.ArgumentParser(prog="codestra-mission-router")
    parser.add_argument("--db", default=".runtime/mission-control.db")
    parser.add_argument(
        "--config-root",
        default="config/router",
        help="router hierarchy profile directory",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed")

    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--repository")

    hierarchy = sub.add_parser("hierarchy")
    hierarchy.add_argument("--repository", required=True)

    tasks = sub.add_parser("tasks")
    tasks.add_argument("--repository")
    tasks.add_argument("--state")

    claim = sub.add_parser("claim")
    claim.add_argument("--task", required=True)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--ttl", type=int, default=600)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--task", required=True)
    heartbeat.add_argument("--lease-token", required=True)
    heartbeat.add_argument("--ttl", type=int, default=600)

    complete = sub.add_parser("complete")
    complete.add_argument("--task", required=True)
    complete.add_argument("--lease-token", required=True)
    complete.add_argument("--head-sha", required=True)

    move = sub.add_parser("move-agents")
    move.add_argument("--agent", action="append", required=True)
    move.add_argument("--repository")
    move.add_argument("--ttl", type=int, default=600)

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--task", required=True)
    reconcile.add_argument("--head-sha", required=True)
    reconcile.add_argument("--pr-merged", action="store_true")
    reconcile.add_argument("--ci-green", action="store_true")
    reconcile.add_argument("--post-merge-green", action="store_true")
    reconcile.add_argument("--pr-url")
    reconcile.add_argument("--ci-url")
    reconcile.add_argument("--evidence-json", default="{}")

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8792)

    args = parser.parse_args()
    router = _router(args.db)

    if args.command == "seed":
        counts = seed_registered_repositories(
            router,
            config_root=Path(args.config_root),
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "repositories": len(counts),
                    "areas": sum(counts.values()),
                    "counts": counts,
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "dashboard":
        print(json.dumps(router.dashboard(args.repository), sort_keys=True))
        return

    if args.command == "hierarchy":
        areas = router.hierarchy(args.repository)
        print(
            json.dumps(
                {
                    "repository": args.repository,
                    "area_count": len(areas),
                    "areas": areas,
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "tasks":
        items = router.tasks(repository=args.repository, work_state=args.state)
        print(json.dumps({"count": len(items), "items": items}, sort_keys=True))
        return

    if args.command == "claim":
        lease = router.claim_task(
            args.task,
            args.agent,
            ttl_seconds=args.ttl,
        )
        print(json.dumps(asdict(lease), sort_keys=True))
        return

    if args.command == "heartbeat":
        lease = router.heartbeat(
            args.task,
            args.lease_token,
            ttl_seconds=args.ttl,
        )
        print(json.dumps(asdict(lease), sort_keys=True))
        return

    if args.command == "complete":
        router.complete_work(
            args.task,
            args.lease_token,
            head_sha=args.head_sha,
        )
        print(json.dumps(router.task(args.task), sort_keys=True))
        return

    if args.command == "move-agents":
        leases = router.move_agents(
            args.agent,
            repository=args.repository,
            ttl_seconds=args.ttl,
        )
        print(
            json.dumps(
                {
                    "assigned_count": len(leases),
                    "assigned": [asdict(lease) for lease in leases],
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "reconcile":
        result = router.record_reconciliation(
            args.task,
            head_sha=args.head_sha,
            pr_merged=args.pr_merged,
            ci_green=args.ci_green,
            post_merge_green=args.post_merge_green,
            pr_url=args.pr_url,
            ci_url=args.ci_url,
            payload=json.loads(args.evidence_json),
        )
        print(json.dumps(result, sort_keys=True))
        return

    if args.command == "serve":
        server = MissionRouterAPI(router).server(args.host, args.port)
        host, port = server.server_address
        print(
            json.dumps(
                {
                    "ok": True,
                    "service": "mission-control-router-api",
                    "host": host,
                    "port": port,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return


if __name__ == "__main__":
    main()
