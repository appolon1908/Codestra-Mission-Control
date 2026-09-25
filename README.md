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
