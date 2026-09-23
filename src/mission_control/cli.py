
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .controller import MissionController
from .github_merge import GitHubMergeExecutor
from .lease import LeaseManager
from .merge_coordinator import MergeCandidate, MergeCoordinator
from .models import (
    AgentRole,
    ApprovalLevel,
    ConflictClass,
    Mission,
    MissionStatus,
)
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
    claim.add_argument(
        "--role",
        choices=[r.value for r in AgentRole],
        default=AgentRole.WRITER.value,
    )
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

    review_approve = sub.add_parser("review-approve")
    review_approve.add_argument("--mission", required=True)
    review_approve.add_argument("--actor", required=True)
    review_approve.add_argument("--head-sha", required=True)
    review_approve.add_argument("--base-sha")
    review_approve.add_argument("--evidence-json", default="{}")

    verify_approve = sub.add_parser("verify-approve")
    verify_approve.add_argument("--mission", required=True)
    verify_approve.add_argument("--actor", required=True)
    verify_approve.add_argument("--head-sha", required=True)
    verify_approve.add_argument("--base-sha")
    verify_approve.add_argument("--evidence-json", default="{}")

    merge_evaluate = sub.add_parser("merge-evaluate")
    merge_evaluate.add_argument("--mission", required=True)
    merge_evaluate.add_argument("--repository", required=True)
    merge_evaluate.add_argument("--pr-number", type=int)
    merge_evaluate.add_argument("--head-sha", required=True)
    merge_evaluate.add_argument("--base-sha")
    merge_evaluate.add_argument("--target-sha")
    merge_evaluate.add_argument("--mergeable", action="store_true")
    merge_evaluate.add_argument("--ci-green", action="store_true")
    merge_evaluate.add_argument("--protected-rules-allow", action="store_true")
    merge_evaluate.add_argument("--base-current", action="store_true")
    merge_evaluate.add_argument("--unresolved-review-blockers", action="store_true")
    merge_evaluate.add_argument("--control-sync-current", action="store_true")
    merge_evaluate.add_argument(
        "--conflict-class",
        choices=[item.value for item in ConflictClass],
        default=ConflictClass.NONE.value,
    )
    merge_evaluate.add_argument("--conflict-file", action="append", default=[])
    merge_evaluate.add_argument("--conflict-summary", default="")
    merge_evaluate.add_argument("--priority", type=int, default=50)
    merge_evaluate.add_argument("--actor", default="merge-coordinator")

    merge_record = sub.add_parser("merge-record")
    merge_record.add_argument("--mission", required=True)
    merge_record.add_argument("--merge-sha", required=True)
    merge_record.add_argument("--method", default="squash")
    merge_record.add_argument("--actor", default="merge-coordinator")

    merge_execute = sub.add_parser("merge-execute")
    merge_execute.add_argument("--mission", required=True)
    merge_execute.add_argument(
        "--method",
        choices=["merge", "squash", "rebase"],
        default="squash",
    )
    merge_execute.add_argument("--actor", default="merge-coordinator")

    add_dependency = sub.add_parser("merge-dependency")
    add_dependency.add_argument("--mission", required=True)
    add_dependency.add_argument("--depends-on", required=True)
    add_dependency.add_argument("--required-merge-sha")

    sub.add_parser("merge-queue")

    dispatch_requests = sub.add_parser("dispatch-requests")
    dispatch_requests.add_argument("--role", choices=[r.value for r in AgentRole])

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
        level = ApprovalLevel(args.level)
        if level >= ApprovalLevel.MERGE:
            raise SystemExit(
                "merge-or-higher cannot be approved through the legacy command; "
                "use review-approve, verify-approve, merge-evaluate, then the "
                "staging/production certification path"
            )
        approval_id = store.record_approval(
            args.mission,
            level,
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
                    "merge_queue": (
                        dict(store.get_merge_queue_item(args.mission))
                        if store.get_merge_queue_item(args.mission)
                        else None
                    ),
                    "sha_approvals": [
                        dict(row)
                        for row in store.valid_sha_approvals(
                            args.mission,
                            mission["head_sha"] or "",
                        )
                    ],
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "review-approve":
        approval_id = MergeCoordinator(store).record_review_acceptance(
            args.mission,
            actor=args.actor,
            head_sha=args.head_sha,
            base_sha=args.base_sha,
            evidence=json.loads(args.evidence_json),
        )
        print(json.dumps({"ok": True, "approval_id": approval_id, "gate": "REVIEW"}))
        return

    if args.command == "verify-approve":
        approval_id = MergeCoordinator(store).record_verification_acceptance(
            args.mission,
            actor=args.actor,
            head_sha=args.head_sha,
            base_sha=args.base_sha,
            evidence=json.loads(args.evidence_json),
        )
        print(
            json.dumps(
                {"ok": True, "approval_id": approval_id, "gate": "VERIFICATION"}
            )
        )
        return

    if args.command == "merge-evaluate":
        candidate = MergeCandidate(
            mission_id=args.mission,
            repository=args.repository,
            pr_number=args.pr_number,
            head_sha=args.head_sha,
            base_sha=args.base_sha,
            target_sha=args.target_sha,
            mergeable=args.mergeable,
            ci_green=args.ci_green,
            protected_rules_allow=args.protected_rules_allow,
            base_current=args.base_current,
            unresolved_review_blockers=args.unresolved_review_blockers,
            control_sync_current=args.control_sync_current,
            conflict_class=ConflictClass(args.conflict_class),
            conflict_files=tuple(args.conflict_file),
            conflict_summary=args.conflict_summary,
            priority=args.priority,
        )
        result = MergeCoordinator(store).evaluate(
            candidate,
            coordinator_actor=args.actor,
        )
        print(
            json.dumps(
                {
                    "mission_id": result.mission_id,
                    "allowed": result.allowed,
                    "state": result.state.value,
                    "reason": result.reason,
                    "required_role": (
                        result.required_role.value if result.required_role else None
                    ),
                    "invalidated_approvals": result.invalidated_approvals,
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "merge-record":
        result_id = MergeCoordinator(store).record_merge(
            args.mission,
            merge_sha=args.merge_sha,
            merge_method=args.method,
            actor=args.actor,
        )
        print(json.dumps({"ok": True, "merge_result_id": result_id}))
        return

    if args.command == "merge-execute":
        result = GitHubMergeExecutor(store).merge(
            args.mission,
            merge_method=args.method,
            actor=args.actor,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "mission_id": result.mission_id,
                    "repository": result.repository,
                    "pr_number": result.pr_number,
                    "head_sha": result.head_sha,
                    "merge_sha": result.merge_sha,
                    "merge_method": result.merge_method,
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "merge-dependency":
        store.add_merge_dependency(
            args.mission,
            args.depends_on,
            required_merge_sha=args.required_merge_sha,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "mission": args.mission,
                    "depends_on": args.depends_on,
                }
            )
        )
        return

    if args.command == "merge-queue":
        print(
            json.dumps(
                {"merge_queue": [dict(row) for row in store.list_merge_queue()]},
                sort_keys=True,
            )
        )
        return

    if args.command == "dispatch-requests":
        role = AgentRole(args.role) if args.role else None
        print(
            json.dumps(
                {
                    "dispatch_requests": [
                        dict(row)
                        for row in store.pending_dispatch_requests(role=role)
                    ]
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
