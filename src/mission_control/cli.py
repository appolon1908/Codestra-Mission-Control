
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .controller import MissionController
from .fabric_api import FabricAPI, FabricRuntime, health_payload, policy_payload
from .lease import LeaseManager
from .models import AgentRole, ApprovalLevel, Mission, MissionStatus
from .network_fabric import (
    DEFAULT_MAX_SNAPSHOT_AGE_SECONDS,
    DEFAULT_OFFLINE_STALE_AFTER_SECONDS,
    FabricConfigError,
    FabricState,
    evaluate_health,
    load_inventory_file,
    load_policy_file,
    read_status_command,
    read_status_file,
    validate_policy,
)
from .policy import ApprovalPolicy
from .store import MissionStore


def _store(path: str) -> MissionStore:
    store = MissionStore(Path(path))
    store.initialize()
    return store


def _fabric_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inventory", default="config/tailscale.nodes.json")
    parser.add_argument(
        "--status-json",
        help="read a captured `tailscale status --json` file instead of running the CLI",
    )
    parser.add_argument("--tailscale-bin", default="tailscale")
    parser.add_argument("--max-snapshot-age", type=int, default=DEFAULT_MAX_SNAPSHOT_AGE_SECONDS)
    parser.add_argument(
        "--offline-stale-after", type=int, default=DEFAULT_OFFLINE_STALE_AFTER_SECONDS
    )


def _status_reader(args: argparse.Namespace):
    if args.status_json:
        return lambda: read_status_file(args.status_json)
    return lambda: read_status_command(binary=args.tailscale_bin)


def _run_fabric_command(args: argparse.Namespace) -> None:
    if args.command == "fabric-policy-validate":
        result = validate_policy(load_policy_file(args.policy))
        print(json.dumps(policy_payload(result), sort_keys=True))
        raise SystemExit(0 if result.valid else 2)

    inventory = load_inventory_file(args.inventory)
    if args.command == "fabric-health":
        report = evaluate_health(
            inventory,
            _status_reader(args)(),
            max_snapshot_age_seconds=args.max_snapshot_age,
            offline_stale_after_seconds=args.offline_stale_after,
        )
        print(json.dumps(health_payload(report), sort_keys=True))
        raise SystemExit(0 if report.state is FabricState.HEALTHY else 2)

    runtime = FabricRuntime(
        inventory,
        load_policy_file(args.policy),
        _status_reader(args),
        max_snapshot_age_seconds=args.max_snapshot_age,
        offline_stale_after_seconds=args.offline_stale_after,
    )
    server = FabricAPI(runtime).server(args.host, args.port)
    host, port = server.server_address[:2]
    print(
        json.dumps(
            {"ok": True, "service": "mission-control-fabric-api", "url": f"http://{host}:{port}"}
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


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

    fabric_health = sub.add_parser("fabric-health")
    _fabric_arguments(fabric_health)

    fabric_policy = sub.add_parser("fabric-policy-validate")
    fabric_policy.add_argument("--policy", default="config/tailscale.policy-plan.json")

    fabric_api = sub.add_parser("serve-fabric-api")
    _fabric_arguments(fabric_api)
    fabric_api.add_argument("--policy", default="config/tailscale.policy-plan.json")
    fabric_api.add_argument("--host", default="127.0.0.1")
    fabric_api.add_argument("--port", type=int, default=8791)

    args = parser.parse_args()
    if args.command in {"fabric-health", "fabric-policy-validate", "serve-fabric-api"}:
        try:
            _run_fabric_command(args)
        except FabricConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
            raise SystemExit(2) from None
        return
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


if __name__ == "__main__":
    main()
