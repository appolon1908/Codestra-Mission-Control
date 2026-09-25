
from __future__ import annotations

import argparse
import json
import uuid
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
from .implementation_api import ImplementationAPI
from .lease import LeaseManager
from .models import AgentRole, ApprovalLevel, Mission, MissionStatus
from .policy import ApprovalPolicy
from .store import MissionStore
from .worker_node import (
    NodeNotFound,
    NodeValidationError,
    WorkerLane,
    WorkerNodeRegistry,
    probe_local_capabilities,
)


def _store(path: str) -> MissionStore:
    store = MissionStore(Path(path))
    store.initialize()
    return store


def _provider_probes(store: MissionStore, runtime_root: Path, providers: list[str]) -> dict:
    from .adapters.claude import ClaudeAdapter
    from .adapters.codex import CodexAdapter

    adapter_types = {"claude": ClaudeAdapter, "codex": CodexAdapter}
    probes: dict = {}
    for provider in providers:
        adapter_type = adapter_types[provider]

        def probe(provider: str = provider, adapter_type=adapter_type) -> dict:
            adapter = adapter_type(store, runtime_root=runtime_root / provider)
            return adapter.auth_status()

        probes[provider] = probe
    return probes


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
    node_register = sub.add_parser("node-register")
    node_register.add_argument("--node", required=True)
    node_register.add_argument("--capabilities-json", required=True)

    node_probe = sub.add_parser("node-probe")
    node_probe.add_argument("--node", required=True)
    node_probe.add_argument(
        "--lane",
        action="append",
        choices=[lane.value for lane in WorkerLane],
        default=[],
    )
    node_probe.add_argument(
        "--provider", action="append", choices=["claude", "codex"], default=[]
    )
    node_probe.add_argument("--worktree-root", required=True)
    node_probe.add_argument("--tailnet-dns")
    node_probe.add_argument("--max-parallel", type=int, default=3)
    node_probe.add_argument("--runtime-root", default=".runtime/worker-node")
    node_probe.add_argument("--skip-auth", action="store_true")

    node_heartbeat = sub.add_parser("node-heartbeat")
    node_heartbeat.add_argument("--node", required=True)

    node_status = sub.add_parser("node-status")
    node_status.add_argument("--node", required=True)

    sub.add_parser("nodes")

    node_api = sub.add_parser("serve-worker-node-api")
    node_api.add_argument("--host", default="127.0.0.1")
    node_api.add_argument("--port", type=int, default=8791)

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

    if args.command.startswith("node") or args.command == "serve-worker-node-api":
        registry = WorkerNodeRegistry(store)
        try:
            payload = _node_command(args, store, registry)
        except NodeValidationError as exc:
            raise SystemExit(json.dumps({"error": "invalid_worker_node", "errors": exc.errors}))
        except NodeNotFound as exc:
            raise SystemExit(json.dumps({"error": "node_not_found", "node_id": exc.args[0]}))
        if payload is not None:
            print(json.dumps(payload, sort_keys=True))
        return


def _node_command(args, store: MissionStore, registry: WorkerNodeRegistry) -> dict | None:
    if args.command == "node-register":
        return registry.register(args.node, json.loads(args.capabilities_json))

    if args.command == "node-probe":
        body = probe_local_capabilities(
            args.node,
            lanes=args.lane or [WorkerLane.BUILDER.value],
            providers=args.provider or ["claude", "codex"],
            worktree_root=args.worktree_root,
            tailnet_dns=args.tailnet_dns,
            max_parallel_writers=args.max_parallel,
        )
        registry.register(args.node, body)
        registry.heartbeat(args.node)
        if not args.skip_auth:
            registry.probe_auth(
                args.node,
                _provider_probes(store, Path(args.runtime_root), body["providers"]),
            )
        return registry.snapshot(args.node)

    if args.command == "node-heartbeat":
        at = registry.heartbeat(args.node)
        return {
            "node_id": args.node,
            "last_heartbeat_at": at,
            "readiness": registry.readiness(args.node).as_payload(),
        }

    if args.command == "node-status":
        return registry.snapshot(args.node)

    if args.command == "nodes":
        items = registry.list()
        return {
            "count": len(items),
            "implementation_ready": sum(
                1 for item in items if item["readiness"]["implementation_ready"]
            ),
            "items": items,
        }

    if args.command == "serve-worker-node-api":
        from .worker_node_api import WorkerNodeAPI

        server = WorkerNodeAPI(store).server(args.host, args.port)
        print(
            json.dumps({"ok": True, "host": args.host, "port": server.server_address[1]}),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return None
    return None


if __name__ == "__main__":
    main()
