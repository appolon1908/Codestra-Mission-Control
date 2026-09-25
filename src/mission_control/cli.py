
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .controller import MissionController
from .inventory import (
    KINDS,
    STATUSES,
    InventoryScanner,
    RepositoryTarget,
    filter_report,
    latest_report,
    load_expected_hosts,
    registry_targets,
    tailscale_status_file,
    tailscale_status_live,
)
from .inventory_api import InventoryAPI
from .lease import LeaseManager
from .models import AgentRole, ApprovalLevel, Mission, MissionStatus
from .policy import ApprovalPolicy
from .store import MissionStore


def _store(path: str) -> MissionStore:
    store = MissionStore(Path(path))
    store.initialize()
    return store


def _add_inventory_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="extra repository checkout to scan (repeatable)",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="do not scan repositories from the registry table",
    )
    parser.add_argument("--nodes-config", help="expected hosts (tailscale.nodes.json)")
    tailnet = parser.add_mutually_exclusive_group()
    tailnet.add_argument("--tailscale-status", help="tailscale status --json capture file")
    tailnet.add_argument(
        "--tailscale-live",
        action="store_true",
        help="read-only `tailscale status --json` on this host",
    )


def _inventory_runner(store: MissionStore, args: argparse.Namespace, *, persist: bool):
    extra: list[RepositoryTarget] = []
    for raw in args.repo:
        name, sep, path = raw.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise SystemExit(f"--repo must be NAME=PATH, got: {raw}")
        extra.append(RepositoryTarget(name.strip(), path.strip()))
    expected = load_expected_hosts(args.nodes_config) if args.nodes_config else []
    provider = None
    if args.tailscale_status:
        provider = tailscale_status_file(args.tailscale_status)
    elif args.tailscale_live:
        provider = tailscale_status_live()
    scanner = InventoryScanner(store=store)

    def run() -> dict:
        targets = [] if args.no_registry else registry_targets(store)
        explicit = {target.name for target in extra}
        targets = [target for target in targets if target.name not in explicit] + extra
        return scanner.scan(
            repositories=targets,
            expected_hosts=expected,
            status_provider=provider,
            persist=persist,
        )

    return run


def main() -> None:
    parser = argparse.ArgumentParser(prog="codestra-mission-control")
    parser.add_argument("--db", default=".runtime/mission-control.db")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    create = sub.add_parser("create-mission")
    create.add_argument("--mission", required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--branch")
    create.add_argument("--worktree")
    create.add_argument("--base-sha")
    create.add_argument("--approval", type=int, default=int(ApprovalLevel.LOCAL_WRITE))

    claim = sub.add_parser("claim")
    claim.add_argument("--mission", required=True)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--role", choices=[r.value for r in AgentRole], default=AgentRole.WRITER.value)
    claim.add_argument("--ttl", type=int, default=600)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--mission", required=True)
    heartbeat.add_argument("--agent", required=True)
    heartbeat.add_argument("--ttl", type=int, default=600)

    release = sub.add_parser("release")
    release.add_argument("--mission", required=True)
    release.add_argument("--agent", required=True)
    release.add_argument(
        "--status",
        choices=[s.value for s in MissionStatus],
        default=MissionStatus.IN_REVIEW.value,
    )

    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--mission", required=True)
    checkpoint.add_argument("--agent", required=True)
    checkpoint.add_argument("--state", required=True)
    checkpoint.add_argument("--head-sha")
    checkpoint.add_argument("--dirty-count", type=int)
    checkpoint.add_argument("--tests-json", default="{}")
    checkpoint.add_argument("--blocker", action="append", default=[])
    checkpoint.add_argument("--request-next-task", action="store_true")

    approve = sub.add_parser("approve")
    approve.add_argument("--mission", required=True)
    approve.add_argument("--level", type=int, required=True)
    approve.add_argument("--actor", required=True)

    policy = sub.add_parser("policy")
    policy.add_argument("--mission", required=True)
    policy.add_argument("--action", required=True)

    status = sub.add_parser("status")
    status.add_argument("--mission", required=True)

    sub.add_parser("expired")
    sub.add_parser("repositories")

    inventory_scan = sub.add_parser("inventory-scan")
    _add_inventory_source_args(inventory_scan)
    inventory_scan.add_argument("--no-persist", action="store_true")

    inventory_drift = sub.add_parser("inventory-drift")
    inventory_drift.add_argument("--kind", action="append", choices=KINDS, default=[])
    inventory_drift.add_argument("--status", action="append", choices=STATUSES, default=[])
    inventory_drift.add_argument("--repository", action="append", default=[])

    inventory_api = sub.add_parser("serve-inventory-api")
    _add_inventory_source_args(inventory_api)
    inventory_api.add_argument("--host", default="127.0.0.1")
    inventory_api.add_argument("--port", type=int, default=8791)

    args = parser.parse_args()
    store = _store(args.db)

    if args.command == "init":
        print(json.dumps({"ok": True, "db": str(store.path)}))
        return

    if args.command == "create-mission":
        store.upsert_mission(
            Mission(
                mission_id=args.mission,
                repository=args.repository,
                goal=args.goal,
                branch=args.branch,
                worktree=args.worktree,
                base_sha=args.base_sha,
                required_approval=ApprovalLevel(args.approval),
            )
        )
        print(json.dumps({"ok": True, "mission": args.mission}))
        return

    leases = LeaseManager(store)

    if args.command == "claim":
        result = leases.claim(
            args.mission,
            args.agent,
            role=AgentRole(args.role),
            ttl_seconds=args.ttl,
        )
        print(json.dumps(result.__dict__, sort_keys=True))
        return

    if args.command == "heartbeat":
        expiry = leases.heartbeat(args.mission, args.agent, ttl_seconds=args.ttl)
        print(json.dumps({"ok": True, "expires_at": expiry}))
        return

    if args.command == "release":
        leases.release(args.mission, args.agent, next_status=MissionStatus(args.status))
        print(json.dumps({"ok": True, "status": args.status}))
        return

    if args.command == "checkpoint":
        checkpoint_id = store.record_checkpoint(
            args.mission,
            args.agent,
            args.state,
            head_sha=args.head_sha,
            dirty_count=args.dirty_count,
            tests=json.loads(args.tests_json),
            blockers=args.blocker,
            next_task_requested=args.request_next_task,
        )
        print(json.dumps({"ok": True, "checkpoint_id": checkpoint_id}))
        return

    if args.command == "approve":
        approval_id = store.record_approval(
            args.mission,
            ApprovalLevel(args.level),
            args.actor,
        )
        print(json.dumps({"ok": True, "approval_id": approval_id}))
        return

    if args.command == "policy":
        decision = ApprovalPolicy(store).evaluate(args.mission, args.action)
        print(
            json.dumps(
                {
                    "allowed": decision.allowed,
                    "required": int(decision.required),
                    "approved": int(decision.approved),
                    "reason": decision.reason,
                }
            )
        )
        return

    if args.command == "status":
        mission = store.get_mission(args.mission)
        if not mission:
            raise SystemExit(f"mission not found: {args.mission}")
        lease = leases.current(args.mission)
        decision = MissionController(store).evaluate(args.mission)
        print(
            json.dumps(
                {
                    "mission": dict(mission),
                    "lease": lease,
                    "controller": {
                        "action": decision.action.value,
                        "reason": decision.reason,
                    },
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "expired":
        print(json.dumps({"expired": leases.expired_missions()}, sort_keys=True))
        return

    if args.command == "repositories":
        rows = [dict(row) for row in store.list_repositories()]
        print(
            json.dumps(
                {
                    "count": len(rows),
                    "local": sum(int(row["local_present"]) for row in rows),
                    "missing": sum(1 for row in rows if not row["local_present"]),
                    "repositories": rows,
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "inventory-scan":
        report = _inventory_runner(store, args, persist=not args.no_persist)()
        print(json.dumps(report, sort_keys=True))
        return

    if args.command == "inventory-drift":
        report = latest_report(store)
        if report is None:
            raise SystemExit("no inventory scan recorded; run inventory-scan first")
        print(
            json.dumps(
                filter_report(
                    report,
                    kinds=args.kind,
                    statuses=args.status,
                    repositories=args.repository,
                ),
                sort_keys=True,
            )
        )
        return

    if args.command == "serve-inventory-api":
        server = InventoryAPI(store, _inventory_runner(store, args, persist=True)).server(
            args.host, args.port
        )
        print(json.dumps({"ok": True, "listening": list(server.server_address)}))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return


if __name__ == "__main__":
    main()
