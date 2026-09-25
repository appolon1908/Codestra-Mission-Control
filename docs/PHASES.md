# Mission Control Delivery Phases

## MC-00 — Durable runtime foundation
SQLite ledger, leases, heartbeat, takeover, checkpoints, approvals, CLI and tests.

## MC-01 — Temporal durable scheduler
Temporal server/namespace, workflow worker, retries, timers, signals and recovery.

## MC-02 — Agent adapters
Codex App Server/SDK adapter, Claude adapter, worker capability registry, stop/resume.

## MC-03 — Control-surface synchronization
Linear mission adapter, Notion continuity adapter, GitHub PR/CI adapter.

## MC-04 — Worktree executor
Create/reuse worktrees, file fences, clean/dirty protection, exact-head verification.

## MC-05 — Approval and certification engine
Reviewer lane, staging gate, production human gate, evidence bundle.

## MC-06 — 24/7 operations
Watchdog, escalation timers, reminders, morning report, stuck-agent takeover,
three-agent scheduling and service observability.

Implemented runtime readback (`mission_control.watchdog_api`, loopback by default,
served by `watchdog_runtime --api-port`):

- `GET /health`
- `GET /platform/v1/watchdog/snapshot` — workers, stale leases, blocked lanes,
  open escalations and recent successor dispatches in one payload.
- `GET /platform/v1/watchdog/workers` — per-slot ACTIVE/STALE/IDLE/DISABLED state,
  lease heartbeat age and latest agent execution; unmanaged leases listed separately.
- `GET /platform/v1/watchdog/leases?state=STALE,EXPIRED` (default) or `ACTIVE`.
- `GET /platform/v1/watchdog/blocked-lanes` — BLOCKED/NEEDS_DECISION missions with
  last checkpoint blockers and age.
- `GET /platform/v1/watchdog/escalations?state=OPEN,ACKNOWLEDGED&mission_id=`
- `POST /platform/v1/watchdog/escalations/evaluate`
- `POST /platform/v1/watchdog/escalations/{id}/acknowledge` with `{"actor": ...}`.
- `GET /platform/v1/watchdog/dispatches?mission_id=&takeover=1&limit=` — successor
  dispatch evidence (predecessor agent/execution, checkpoint head, new execution).
- `POST /platform/v1/watchdog/tick` with `{"dispatch": true}` — requires
  `--api-allow-dispatch`; otherwise 403.

Escalation levels are REMINDER → ESCALATED → OWNER_DECISION by condition age
(`STALE_HEARTBEAT`, `EXPIRED_LEASE`, `BLOCKED_LANE`, `NEEDS_DECISION_LANE`,
`DISPATCH_FAILED`). Escalations resolve automatically when the condition clears and
are recorded with `delivery=LOCAL_ONLY`; no SMS/email/call delivery is wired.
`codestra-mission-control watchdog-status --worker codex-01:codex --evaluate` gives
the same readback from the CLI.
