
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from .controller import MissionController
from .implementation_api import ImplementationAPI
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

    implementation_start = sub.add_parser("implementation-start")
    implementation_start.add_argument("--execution-id")
    implementation_start.add_argument("--mission", required=True)
    implementation_start.add_argument("--agent", required=True)
    implementation_start.add_argument("--workstation", required=True)
    implementation_start.add_argument("--provider", required=True)
    implementation_start.add_argument("--branch", required=True)
    implementation_start.add_argument("--worktree", required=True)
    implementation_start.add_argument("--api-required", action="store_true")

    implementation_proof = sub.add_parser("implementation-proof")
    implementation_proof.add_argument("--execution", required=True)
    implementation_proof.add_argument("--implementation-file", action="append", default=[])
    implementation_proof.add_argument("--api-endpoint", action="append", default=[])
    implementation_proof.add_argument("--tests-json", default="{}")
    implementation_proof.add_argument("--local-sha")
    implementation_proof.add_argument("--pushed-sha")
    implementation_proof.add_argument("--pr-number", type=int)
    implementation_proof.add_argument("--pr-url")
    implementation_proof.add_argument("--pr-head-sha")

    implementation_status = sub.add_parser("implementation-status")
    implementation_status.add_argument("--execution", required=True)

    implementation_list = sub.add_parser("implementation-list")
    implementation_list.add_argument("--state", action="append", default=[])

    implementation_api = sub.add_parser("serve-implementation-api")
    implementation_api.add_argument("--host", default="127.0.0.1")
    implementation_api.add_argument("--port", type=int, default=8790)

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

    if args.command == "implementation-start":
        execution_id = args.execution_id or f"impl-{uuid.uuid4().hex}"
        agent_number = store.start_implementation_execution(
            execution_id=execution_id,
            mission_id=args.mission,
            agent_id=args.agent,
            workstation=args.workstation,
            provider=args.provider,
            branch=args.branch,
            worktree=args.worktree,
            api_required=args.api_required,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "execution_id": execution_id,
                    "agent_number": agent_number,
                    "state": "STARTED",
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "implementation-proof":
        decision = store.record_implementation_proof(
            args.execution,
            implementation_files=args.implementation_file,
            api_endpoints=args.api_endpoint,
            tests=json.loads(args.tests_json),
            local_commit_sha=args.local_sha,
            pushed_branch_sha=args.pushed_sha,
            pr_number=args.pr_number,
            pr_url=args.pr_url,
            pr_head_sha=args.pr_head_sha,
        )
        print(
            json.dumps(
                {
                    "ok": decision.eligible_for_review,
                    "state": decision.state.value,
                    "push_proven": decision.push_proven,
                    "eligible_for_review": decision.eligible_for_review,
                    "reasons": list(decision.reasons),
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "implementation-status":
        row = store.get_implementation_execution(args.execution)
        if not row:
            raise SystemExit(f"implementation execution not found: {args.execution}")
        payload = dict(row)
        for key in ("implementation_files_json", "api_endpoints_json", "tests_json"):
            payload[key.removesuffix("_json")] = json.loads(payload.pop(key))
        payload["api_required"] = bool(payload["api_required"])
        payload["proof_matched"] = bool(payload["proof_matched"])
        print(json.dumps(payload, sort_keys=True))
        return

    if args.command == "implementation-list":
        states = tuple(args.state) if args.state else None
        rows = [dict(row) for row in store.list_implementation_executions(states=states)]
        print(
            json.dumps(
                {
                    "count": len(rows),
                    "proven": sum(row["state"] == "PROVEN" for row in rows),
                    "needs_rework": sum(
                        row["state"] == "NEEDS_REWORK" for row in rows
                    ),
                    "executions": rows,
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "serve-implementation-api":
        server = ImplementationAPI(store).server(args.host, args.port)
        host, port = server.server_address
        print(
            json.dumps(
                {
                    "ok": True,
                    "service": "mission-control-implementation-api",
                    "host": host,
                    "port": port,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        server.serve_forever()
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
