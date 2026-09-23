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
Reviewer lane, exact-SHA approvals, staging gate, production human gate,
evidence bundle.

## MC-05B — Merge Coordinator / PAS-258
Persistent SHA-bound Reviewer and Verifier approvals, stale-approval
invalidation, conflict-class records, dependency-aware merge queue,
protected merge predicate, merge result ledger and automatic
Builder/Reviewer/Verifier redispatch requests.

## MC-06 — 24/7 operations
Watchdog, escalation timers, reminders, morning report, stuck-agent takeover,
four-level scheduling, 2-minute handoff watching, 5-minute/3-attempt notification escalation, and service observability.
