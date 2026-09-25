
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .controller import MissionController
from .lease import LeaseManager
from .models import AgentRole, ApprovalLevel, Mission, MissionStatus
from .policy import ApprovalPolicy
from .repo_sync import SELF_HOSTED_REMOTE, Readiness, RepoSyncError, RepoSyncInspector
from .repo_sync_api import RepoSyncAPI
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

    sync_status = sub.add_parser("repo-sync-status")
    sync_status.add_argument("--repo", required=True)
    sync_status.add_argument("--live", action="store_true")

    sub.add_parser("auth-readiness")

    publish = sub.add_parser("publish-readiness")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--remote", default=SELF_HOSTED_REMOTE)
    publish.add_argument("--live", action="store_true")

    sync_api = sub.add_parser("serve-repo-sync-api")
    sync_api.add_argument("--host", default="127.0.0.1")
    sync_api.add_argument("--port", type=int, default=8791)

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

    if args.command in {"repo-sync-status", "publish-readiness", "auth-readiness"}:
        inspector = RepoSyncInspector()
        if args.command == "auth-readiness":
            auth = inspector.github_auth()
            print(
                json.dumps(
                    {
                        "github": {
                            "state": auth.state.value,
                            "detail": auth.detail,
                            "account": auth.account,
                        },
                        "self_hosted_remote": SELF_HOSTED_REMOTE,
                        "self_hosted_requires_github_auth": False,
                    },
                    sort_keys=True,
                )
            )
            return
        try:
            if args.command == "repo-sync-status":
                report = inspector.report(args.repo, live=args.live)
                print(json.dumps(report.to_dict(), sort_keys=True))
                return
            status = inspector.status(args.repo, live=args.live)
        except RepoSyncError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
            raise SystemExit(3) from exc
        sync = status.remote(args.remote)
        auth = inspector.github_auth() if sync and sync.kind == "GITHUB" else None
        decision = inspector.publish_decision(status, args.remote, auth)
        print(json.dumps(asdict(decision), sort_keys=True))
        if decision.readiness == Readiness.BLOCKED:
            raise SystemExit(2)
        return

    if args.command == "serve-repo-sync-api":
        server = RepoSyncAPI(store, RepoSyncInspector()).server(args.host, args.port)
        host, port = server.server_address
        print(
            json.dumps(
                {
                    "ok": True,
                    "service": "mission-control-repo-sync-api",
                    "host": host,
                    "port": port,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        server.serve_forever()
        return


if __name__ == "__main__":
    main()
