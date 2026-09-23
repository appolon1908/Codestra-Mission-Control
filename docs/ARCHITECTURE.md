# 24/7 Mission Control Architecture

## Control loop

1. Linear exposes the highest-priority READY mission.
2. Mission Control verifies local Git + GitHub + architecture dependencies.
3. Controller creates or selects an isolated worktree.
4. Exactly one writer lease is issued.
5. Agent heartbeats while implementing.
6. Agent emits checkpoints.
7. If heartbeat expires, Mission Control records the last known SHA and reassigns.
8. Independent Reviewer evaluates the exact pushed HEAD.
9. Independent Verifier records exact-SHA test/runtime evidence.
10. Merge Coordinator invalidates stale approvals, classifies conflicts, checks
    dependency order and protected GitHub gates, and authorizes only the exact
    reviewed + verified HEAD.
11. External GitHub execution performs the merge and returns the merge SHA.
12. Approval policy gates staging and production-ready certification.
13. Mission Control updates Linear + Notion + GitHub and selects the successor.

## Agent lanes

- Builder — sole implementation writer.
- Reviewer — independent diff/security/architecture review.
- Verifier — independent tests, integration/runtime certification and evidence.
- Merge Coordinator — deterministic exact-SHA approval authority, conflict
  classifier and dependency-aware merge queue.

Reviewer and Verifier may be Claude, Copilot, Codex or another approved adapter,
but approvals are persisted against the exact PR HEAD SHA. A later push makes
older approval evidence stale. The Merge Coordinator is not another feature
writer and cannot bypass protected GitHub rules.

Only one role lease is active for a mission at a time. Reviewer/Verifier
redispatch reuses the exact clean mission worktree; Builder conflict resolution
returns to the existing mission rather than creating an unrelated task.

## Durable state

SQLite is the Phase 0 local durable ledger. Temporal becomes the durable scheduler in
Phase 1. SQLite remains useful for workstation state, audit and recovery.

## Handoff invariant

Every takeover must be reconstructible from mission id, repository, worktree, branch,
base SHA, last known HEAD, dirty state, checkpoint, test results, blockers and approval level.

## Production invariant

No agent can self-authorize production effects. Production actions require an explicit
human approval record at PRODUCTION_EFFECT.


## Merge Coordinator invariant

A mission is MERGE_READY only when:

- observed PR head equals the reviewed head;
- observed PR head equals the verified head;
- required CI/checks are green;
- protected GitHub rules permit merge;
- base/target freshness is acceptable;
- no unresolved review blockers remain;
- Linear + Notion + Mission Control checkpoint is current;
- declared merge dependencies are already satisfied;
- Reviewer and Verifier are independent actors.

Conflict classes fail closed:

- CLASS-0: no conflict;
- CLASS-1: explicit mechanical conflict -> Builder resolution;
- CLASS-2: semantic/API/auth/schema/business conflict -> Builder + re-review + re-verify;
- CLASS-3: cross-repository convergence conflict -> ordered convergence mission;
- CLASS-4: unknown/unsafe -> BLOCKED_CONFLICT with no guessing.
