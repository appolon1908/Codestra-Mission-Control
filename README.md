# Codestra Mission Control

Durable 24/7 orchestration control plane for Codestra coding agents.

## Source-of-truth model

- Linear — live mission queue, priority, blockers and next-task authority.
- GitHub — code, branch, pull request, CI and review authority.
- Notion — architecture, decisions, runbooks and continuity.
- Mission Control — dispatch, leases, heartbeat, handoff and acceptance authority.
- SentinelX — workstation/server execution and verification.
- Codex / Claude / other agents — replaceable workers.

## Phase 0

The foundation implements:

- one active writer lease per mission;
- heartbeat and lease expiry;
- deterministic takeover when a writer disappears;
- structured checkpoints and handoffs;
- action approval levels;
- persistent SQLite event ledger;
- CLI for local automation;
- adapter contract for Codex, Claude and future workers;
- tests for anti-clash and takeover behavior.

## Safety

Mission Control never treats an agent self-report as certification.

Production effects, production database writes, live billing, live calling,
live SMS/email and destructive Git operations require explicit policy approval.

## Quick start

    python -m mission_control.cli --db .runtime/mission-control.db init
    python -m mission_control.cli --db .runtime/mission-control.db create-mission --mission PAS-29 --repository Codestra-Mission-Control --goal "Build event-driven IDE agent dispatcher"
    python -m mission_control.cli --db .runtime/mission-control.db claim --mission PAS-29 --agent codex-01 --ttl 600
    python -m mission_control.cli --db .runtime/mission-control.db heartbeat --mission PAS-29 --agent codex-01 --ttl 600

## Checkpoint-to-next-task dispatcher

`mission_control.checkpoint_dispatch.CheckpointDispatcher` consumes
`CHECKPOINT_RECORDED` ledger events through a durable cursor and classifies each
checkpoint:

- `BLOCKED` — checkpoint carries blockers or a blocking state; mission moves to `BLOCKED`.
- `REWORK` — next task requested but delivery proof is missing or invalid (dirty
  worktree, failing tests, missing files/endpoints, or checkpoint head, local
  commit, pushed branch and PR head SHAs do not all match).
- `READY_FOR_NEXT` — proof verified; parent moves to `IN_REVIEW` (never
  self-certified) and a `QUEUED` successor mission `<root>-S<n>` based on the
  proven head is persisted for the scheduler.
- `CONTINUE` — progress checkpoint without a next-task request.

Missions whose acceptance contains `api_required` also need API endpoint evidence.

    python -m mission_control.cli --db .runtime/mission-control.db dispatch-proof --mission PAS-29 --agent claude-05 --implementation-file src/x.py --tests-json '{"passed": true}' --local-sha <sha> --pushed-sha <sha> --pr-head-sha <sha> --pr-url <url>
    python -m mission_control.cli --db .runtime/mission-control.db dispatch-run
    python -m mission_control.cli --db .runtime/mission-control.db dispatch-serve --port 8765

Loopback API (`dispatch-serve`):

- `GET  /platform/v1/dispatcher/state`
- `GET  /platform/v1/dispatcher/decisions?mission_id=&limit=`
- `GET  /platform/v1/dispatcher/tasks?status=`
- `GET  /platform/v1/dispatcher/proofs?mission_id=`
- `POST /platform/v1/dispatcher/proofs`
- `POST /platform/v1/dispatcher/run`

The watchdog runtime runs the dispatcher before every scheduler tick.

## Inventory & drift (PAS-5)

Read-only scan of every registered repository and all of its worktrees, expected
tailnet hosts, and runtime leases/executions. The scanner never fetches, pulls or
writes the index; `behind` is relative to the last fetch, so `fetch_age_seconds`
and `remote_refs_stale`/`never_fetched` are reported alongside it.

Each subject is scanned in isolation. A failed subject is `ERROR` with an exact
`{code, message}` and carries `last_verified` (non-authoritative) from the most
recent successful scan; it never collapses the rest of the report. Statuses are
`OK`, `DRIFT`, `STALE`, `ABSENT`, `ERROR`.

Worktree flags: `dirty`, `untracked`, `conflicted`, `ahead`, `behind`, `diverged`,
`local_only`, `pending_push`, `upstream_gone`, `detached_head`, `no_commits`
(`no_upstream` and `worktree_locked` are informational). Host flags:
`host_offline`, `host_last_seen_stale`, `host_missing_from_tailnet`,
`host_unexpected`. Runtime flags: `lease_expired`, `heartbeat_stale`,
`execution_stale`.

    python -m mission_control.cli --db .runtime/mission-control.db inventory-scan --nodes-config config/tailscale.nodes.json --tailscale-live
    python -m mission_control.cli --db .runtime/mission-control.db inventory-drift --status ERROR
    python -m mission_control.cli --db .runtime/mission-control.db serve-inventory-api --nodes-config config/tailscale.nodes.json --tailscale-live

API (binds 127.0.0.1:8791 by default). `--host` accepts literal loopback
or Tailscale addresses (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`); `localhost`
is normalized to `127.0.0.1`. IPv6 uses an IPv6 listener. Wildcard, public,
LAN and arbitrary DNS addresses are rejected before a socket is opened:

- `GET  /health`
- `POST /platform/v1/inventory/drift/scans` — run and persist a scan (201; 409 while a scan runs)
- `GET  /platform/v1/inventory/drift?kind=&status=&repository=` — latest scan with `report_stale`/`age_seconds`
- `GET  /platform/v1/inventory/drift/repositories/{repository}` — repository + worktree items
- `GET  /platform/v1/inventory/drift/hosts` — host + runtime items

## Worker nodes (CORE-NODE-02)

Worker nodes register durable capabilities (lanes, providers, worktree root,
tools) and report heartbeat plus provider-auth status. Every node must carry the
`builder` lane; review-only nodes are rejected. Payloads containing
secret-bearing keys are rejected, and auth details are redacted before storage.

    python -m mission_control.cli --db .runtime/mission-control.db node-probe --node core-node-02 --lane builder --lane reviewer --lane verifier --worktree-root /home/codestra/Worktrees
    python -m mission_control.cli --db .runtime/mission-control.db node-status --node core-node-02
    python -m mission_control.cli --db .runtime/mission-control.db serve-worker-node-api --port 8791

HTTP API (`127.0.0.1` by default):

- `GET /health`
- `GET /platform/v1/worker-nodes`
- `GET|PUT /platform/v1/worker-nodes/{node_id}`
- `POST /platform/v1/worker-nodes/{node_id}/heartbeat`
- `GET /platform/v1/worker-nodes/{node_id}/readiness`
- `GET /platform/v1/worker-nodes/{node_id}/provider-auth`
- `PUT /platform/v1/worker-nodes/{node_id}/provider-auth/{provider}`

Readiness is `READY`, `DEGRADED` (implementation-ready with warnings) or
`NOT_READY`. Implementation work requires a fresh heartbeat (300s), git, a
writable worktree root, at least one provider authenticated within the last
hour, and production effects disabled.
