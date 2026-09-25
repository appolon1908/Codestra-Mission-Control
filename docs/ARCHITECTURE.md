# 24/7 Mission Control Architecture

## Control loop

1. Linear exposes the highest-priority READY mission.
2. Mission Control verifies local Git + GitHub + architecture dependencies.
3. Controller creates or selects an isolated worktree.
4. Exactly one writer lease is issued.
5. Agent heartbeats while implementing.
6. Agent emits checkpoints.
7. If heartbeat expires, Mission Control records the last known SHA and reassigns.
8. Independent reviewer evaluates the pushed implementation.
9. Approval policy gates merge, staging and production.
10. Mission Control updates Linear + Notion + GitHub and selects the successor.

## Agent lanes

- Writer — implementation and tests.
- Reviewer — independent diff/security/architecture review.
- Verifier — integration/staging certification and evidence.

A reviewer/verifier is not a second writer unless Mission Control explicitly transfers
the writer lease after the previous lease expires or is released.

## Durable state

SQLite is the Phase 0 local durable ledger. Temporal becomes the durable scheduler in
Phase 1. SQLite remains useful for workstation state, audit and recovery.

## Handoff invariant

Every takeover must be reconstructible from mission id, repository, worktree, branch,
base SHA, last known HEAD, dirty state, checkpoint, test results, blockers and approval level.

## Production invariant

No agent can self-authorize production effects. Production actions require an explicit
human approval record at PRODUCTION_EFFECT.

## Deterministic mission-dispatch protocol

Permanent agent lanes carry exactly one `CURRENT_TASK`. Only Mission Control's coordinator authority may assign or advance a writable lane; Builder/Reviewer/Verifier workers cannot self-dispatch successor work. The newest dated `dispatcher_assignments` record is the authoritative resume point.

A lane can advance only after the current writer owns the WRITER lease and records a clean checkpoint with a HEAD SHA, no blockers, implementation/tests complete, GitHub/Linear/Notion synchronization evidence, a posted dated handoff, and `NEXT_TASK_REQUEST=YES`. Completion clears `CURRENT_TASK`; the coordinator may then publish the next assignment packet defined by `schemas/dispatcher-assignment.schema.json`.

Assignment dates are monotonic per lane so stale handoffs cannot replace newer lane history. Existing Mission Control lease, review, merge, staging, and production approval gates remain unchanged.
