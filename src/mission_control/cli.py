
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from .control_sync import (
    DEFAULT_REQUIRED,
    CheckpointEnvelope,
    Surface,
    SurfaceObservation,
    reconcile,
    utc_now,
)
from .controller import MissionController
from .lease import LeaseManager
from .models import AgentRole, ApprovalLevel, Mission, MissionStatus
from .policy import ApprovalPolicy
from .store import MissionStore


def _store(path: str) -> MissionStore:
    store = MissionStore(Path(path))
    store.initialize()
    return store


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

    observe = sub.add_parser("sync-observe")
    observe.add_argument("--mission", required=True)
    observe.add_argument("--agent", required=True)
    observe.add_argument("--surface", choices=[s.value for s in Surface], required=True)
    observe.add_argument("--status", required=True)
    observe.add_argument("--head-sha")
    observe.add_argument("--error")
    observe.add_argument("--unavailable", action="store_true")
    observe.add_argument("--observed-at", help="ISO-8601 timestamp; defaults to now")

    readback = sub.add_parser("sync-readback")
    readback.add_argument("--mission", required=True)
    readback.add_argument("--head-sha", help="defaults to latest checkpoint, then mission HEAD")
    readback.add_argument("--status", help="defaults to latest checkpoint state")
    readback.add_argument("--request-complete", action="store_true")
    readback.add_argument("--max-age-seconds", type=int, default=900)
    readback.add_argument(
        "--required",
        default=",".join(s.value for s in DEFAULT_REQUIRED),
        help="comma-separated required surfaces",
    )

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

    if args.command == "sync-observe":
        if not store.get_mission(args.mission):
            raise SystemExit(f"mission not found: {args.mission}")
        if not leases.is_owner(args.mission, args.agent):
            raise SystemExit(
                f"ownership refused: {args.agent} does not hold the writer lease "
                f"for {args.mission}"
            )
        observed_at = datetime.fromisoformat(args.observed_at) if args.observed_at else None
        if observed_at is not None and observed_at.tzinfo is None:
            raise SystemExit("--observed-at must include a timezone offset")
        stored = store.record_surface_observation(
            args.mission,
            SurfaceObservation(
                surface=Surface(args.surface),
                available=not args.unavailable,
                status=args.status,
                head_sha=args.head_sha,
                error=args.error,
                observed_at=observed_at or utc_now(),
            ),
            agent_id=args.agent,
        )
        source = reconcile(
            CheckpointEnvelope(args.mission, args.status, None),
            [stored],
            required=(),
        ).sources[0]
        print(json.dumps({"ok": True, "source": source.to_dict()}, sort_keys=True))
        return

    if args.command == "sync-readback":
        mission = store.get_mission(args.mission)
        if not mission:
            raise SystemExit(f"mission not found: {args.mission}")
        latest = store.latest_checkpoint(args.mission)
        head_sha = args.head_sha or (latest["head_sha"] if latest else None) or mission["head_sha"]
        status = args.status or (latest["state"] if latest else mission["status"])
        required = tuple(Surface(x.strip()) for x in args.required.split(",") if x.strip())
        checkpoint = CheckpointEnvelope(args.mission, status, head_sha, args.request_complete)
        decision = reconcile(
            checkpoint,
            store.surface_observations(args.mission),
            required=required,
            max_age=timedelta(seconds=args.max_age_seconds),
        )
        print(
            json.dumps(
                {
                    "mission": args.mission,
                    "checkpoint": {
                        "status": status,
                        "head_sha": head_sha,
                        "request_complete": args.request_complete,
                    },
                    "decision": decision.to_dict(),
                },
                sort_keys=True,
            )
        )
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
