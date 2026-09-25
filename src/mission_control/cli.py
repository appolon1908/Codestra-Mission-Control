
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .controller import MissionController
from .lease import LeaseManager
from .merge_api import MergeCoordinatorAPI
from .merge_coordinator import (
    EvidenceRejected,
    EvidenceRole,
    EvidenceVerdict,
    MergeCoordinator,
    PullRequestSnapshot,
    classify_conflicts,
)
from .models import AgentRole, ApprovalLevel, Mission, MissionStatus
from .policy import ApprovalPolicy
from .store import CompletionBlocked, MissionStore


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

    record_head = sub.add_parser("record-head")
    record_head.add_argument("--mission", required=True)
    record_head.add_argument("--head-sha", required=True)
    record_head.add_argument("--actor", required=True)

    evidence = sub.add_parser("record-evidence")
    evidence.add_argument("--mission", required=True)
    evidence.add_argument("--role", choices=[r.value for r in EvidenceRole], required=True)
    evidence.add_argument("--head-sha", required=True)
    evidence.add_argument("--actor", required=True)
    evidence.add_argument(
        "--verdict", choices=[v.value for v in EvidenceVerdict], required=True
    )
    evidence.add_argument("--blocker", action="append", default=[])

    dependency = sub.add_parser("add-dependency")
    dependency.add_argument("--mission", required=True)
    dependency.add_argument("--depends-on", required=True)

    classify = sub.add_parser("classify-conflicts")
    classify.add_argument(
        "--mergeable", choices=["true", "false", "unknown"], required=True
    )
    classify.add_argument("--path", action="append", default=[])
    classify.add_argument("--cross-repo", action="store_true")

    merge_evaluate = sub.add_parser("merge-evaluate")
    merge_evaluate.add_argument("--mission", required=True)
    merge_evaluate.add_argument(
        "--snapshot-json",
        required=True,
        help="PR snapshot JSON, or @path to read it from a file",
    )

    merge_authorization = sub.add_parser("merge-authorization")
    merge_authorization.add_argument("--mission", required=True)

    merge_api = sub.add_parser("serve-merge-api")
    merge_api.add_argument("--host", default="127.0.0.1")
    merge_api.add_argument("--port", type=int, default=8791)

    sub.add_parser("expired")
    sub.add_parser("repositories")

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
        try:
            leases.release(args.mission, args.agent, next_status=MissionStatus(args.status))
        except CompletionBlocked as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "completion_blocked",
                        "status": args.status,
                        "reasons": exc.reasons,
                    }
                )
            )
            raise SystemExit(2) from None
        print(json.dumps({"ok": True, "status": args.status}))
        return

    coordinator = MergeCoordinator(store)

    if args.command == "record-head":
        print(
            json.dumps(
                coordinator.record_head(args.mission, args.head_sha, args.actor),
                sort_keys=True,
            )
        )
        return

    if args.command == "record-evidence":
        try:
            evidence_id = coordinator.record_evidence(
                args.mission,
                role=EvidenceRole(args.role),
                head_sha=args.head_sha,
                actor=args.actor,
                verdict=EvidenceVerdict(args.verdict),
                blockers=args.blocker,
            )
        except EvidenceRejected as exc:
            print(json.dumps({"ok": False, "error": "evidence_rejected", "detail": str(exc)}))
            raise SystemExit(2) from None
        print(json.dumps({"ok": True, "evidence_id": evidence_id}))
        return

    if args.command == "add-dependency":
        store.add_dependency(args.mission, args.depends_on)
        print(
            json.dumps(
                {"ok": True, "mission": args.mission,
                 "dependencies": store.dependencies(args.mission)}
            )
        )
        return

    if args.command == "classify-conflicts":
        mergeable = {"true": True, "false": False, "unknown": None}[args.mergeable]
        assessment = classify_conflicts(mergeable, args.path, cross_repo=args.cross_repo)
        print(json.dumps(assessment.as_dict(), sort_keys=True))
        return

    if args.command == "merge-evaluate":
        raw = args.snapshot_json
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        decision = coordinator.evaluate(
            args.mission, PullRequestSnapshot.from_dict(json.loads(raw))
        )
        print(json.dumps(decision.as_dict(), sort_keys=True))
        if not decision.merge_allowed:
            raise SystemExit(3)
        return

    if args.command == "merge-authorization":
        authorization = coordinator.authorization(args.mission)
        print(json.dumps(authorization, sort_keys=True))
        if not authorization["authorized"]:
            raise SystemExit(3)
        return

    if args.command == "serve-merge-api":
        server = MergeCoordinatorAPI(store).server(args.host, args.port)
        host, port = server.server_address[:2]
        print(json.dumps({"ok": True, "listening": f"http://{host}:{port}"}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
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
