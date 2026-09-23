# Merge Coordinator / Conflict & Approval Authority

Linear authority: PAS-258

## Purpose

The Merge Coordinator is the fourth controlled level in Codestra Mission Control:

1. Builder — sole code writer.
2. Reviewer — independent exact-SHA review.
3. Verifier — independent exact-SHA test/runtime evidence.
4. Merge Coordinator — deterministic merge authorization and conflict routing.

It is deliberately not a general-purpose coding agent.

## Persistent state

Mission Control stores:

- sha_approvals — Reviewer, Verifier and Merge Coordinator approvals bound to
  exact PR HEAD SHA;
- conflict_records — conflict class, files, head/base SHA, resolution status;
- merge_queue — repository/PR/head/base/target/priority/state;
- merge_dependencies — predecessor mission + optional exact required merge SHA;
- merge_results — immutable merge SHA/result history;
- dispatch_requests — persistent role redispatch requests for Builder,
  Reviewer and Verifier.

All changes also emit ledger events.

## Stale approval invalidation

record_checkpoint() and observe_head() compare the newly observed HEAD with
the mission's current HEAD. Any APPROVED SHA record for a different HEAD becomes
STALE immediately and cannot authorize merge.

A direct legacy numeric MERGE approval cannot authorize a merge. The policy
engine requires MERGE_AUTHORIZATION for the mission's current exact HEAD.

## Conflict policy

Classification never guesses from filenames alone.

- CLASS-0: no conflict.
- CLASS-1: mechanical only when explicitly proven/hinted.
- CLASS-2: semantic conflict explicitly identified.
- CLASS-3: cross-repository conflict.
- CLASS-4: unknown/unsafe; fail closed.

Semantic/cross-repo conflicts create a conflict record and redispatch the
Builder. The resulting new HEAD must be reviewed and verified again.

## Merge-ready predicate

The coordinator evaluates a MergeCandidate. READY requires all of:

- mergeable PR;
- green required CI;
- protected rules allow merge;
- base is current enough for policy;
- no unresolved blocking review comments;
- current Linear/Notion/Mission Control checkpoint;
- exact-head Reviewer approval;
- exact-head Verifier approval by a different actor;
- satisfied merge dependencies.

Only then is an exact-head MERGE_AUTHORIZATION written and mission status set
to MERGE_READY.

The actual GitHub merge is performed by a separate CAS-style GitHub execution
boundary. It sends the exact expected PR HEAD SHA to GitHub, so a moved PR head
is rejected. A successful merge records the returned merge SHA in the durable
ledger. The merge-execute CLI invokes this boundary only from a READY queue item.

## Redispatch

The coordinator persists role requests rather than inventing successor work:

- missing review -> REVIEWER;
- missing verification -> VERIFIER;
- mechanical/semantic/cross-repo conflict -> WRITER;
- CI failure -> WRITER;
- unknown conflict -> no automatic writer dispatch; requires decision.

Temporal activities persist the same requests. The scheduler can consume
Builder/Reviewer/Verifier requests using role-specific worker slots.

## CLI examples

Review acceptance:

    python -m mission_control.cli --db .runtime/mission-control.db review-approve       --mission PAS-258 --actor claude-reviewer --head-sha SHA       --evidence-json '{"review":"PASS"}'

Verification acceptance:

    python -m mission_control.cli --db .runtime/mission-control.db verify-approve       --mission PAS-258 --actor codex-verifier --head-sha SHA       --evidence-json '{"tests":"59 passed"}'

Merge evaluation:

    python -m mission_control.cli --db .runtime/mission-control.db merge-evaluate       --mission PAS-258 --repository ingtrader21-spec/repo --pr-number 123       --head-sha SHA --base-sha BASE --target-sha TARGET       --mergeable --ci-green --protected-rules-allow --base-current       --control-sync-current


Execute an authorized merge:

    python -m mission_control.cli --db .runtime/mission-control.db       merge-execute --mission PAS-258 --method squash

Queue inspection:

    python -m mission_control.cli --db .runtime/mission-control.db merge-queue

Reviewer redispatch inspection:

    python -m mission_control.cli --db .runtime/mission-control.db       dispatch-requests --role REVIEWER

## Production boundary

Merge authorization is not production authorization. Live customer traffic,
payments, calls, SMS/email, provider effects and destructive production
operations remain human-gated.
