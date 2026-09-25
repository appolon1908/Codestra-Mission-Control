# Merge Coordinator (PAS-258 / CORE-AI-02)

Level-4 deterministic conflict and approval authority. It never merges by itself and
never weakens protected checks; it decides whether an exact PR head may merge.

## Rules

- Every decision binds to one exact 40-hex PR head SHA.
- Reviewer and Verifier evidence is accepted only for the mission's current head and only
  from actors who never held the writer lease or recorded a head (no self-review).
- Reviewer and Verifier must be different actors.
- Recording a new head revokes `MERGE_READY`; old evidence becomes stale.
- Any new head, evidence or dependency after a decision supersedes it.
- `merge` and higher policy actions require a recorded approval **and** a current
  `MERGE_READY` decision on the exact mission head.
- Mission status `MERGE_READY` / `COMPLETE` (via `release` or `set_status`) is refused
  without current authorization.

## Merge-ready predicate

All must hold: valid SHAs; head branch is not protected and differs from base; PR branch
matches mission branch (when set); PR head == recorded mission head; latest checkpoint is
on that head with `dirty_count == 0`; `base_sha == target_sha` (base is current);
`mergeable == true` with no conflict paths; accepted, blocker-free, independent Reviewer and
Verifier evidence on the head; no unresolved review threads; required checks declared and
all `success`; `protected_rules_allow == true`; every dependency mission is
`COMPLETE`/`CERTIFIED`/`STAGING`.

## Conflict classes

| Class | Meaning | Next gate |
| --- | --- | --- |
| CLASS-0 NONE | clean | NONE |
| CLASS-1 MECHANICAL | lockfiles, generated artifacts, docs | BUILDER_RESOLVE |
| CLASS-2 SEMANTIC | source, config, schema, migration, auth, API spec | BUILDER_RESOLVE |
| CLASS-3 CROSS_REPO | cross-repository contract conflict | CONVERGENCE |
| CLASS-4 UNSAFE | protected surfaces (`.github/`, CODEOWNERS), unknown files, unsafe paths, unknown mergeability, inconsistent evidence | HUMAN_DECISION |

Gate precedence: HUMAN_DECISION, CONVERGENCE, BUILDER_RESOLVE, REVIEW, VERIFY, CI, DEPENDENCIES.

## HTTP API

`codestra-mission-control --db <db> serve-merge-api --host 127.0.0.1 --port 8791`

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | service + endpoint list |
| POST | `/platform/v1/merge-coordinator/conflicts/classify` | `{mergeable, conflicted_paths, cross_repo}` → class |
| POST | `/platform/v1/merge-coordinator/missions/{id}/head` | `{head_sha, actor}` → head binding, stale evidence count |
| POST | `/platform/v1/merge-coordinator/missions/{id}/evidence` | `{role, head_sha, actor, verdict, blockers}` → 201, or 409 if rejected |
| POST | `/platform/v1/merge-coordinator/missions/{id}/dependencies` | `{depends_on}` → 201, 400 on cycle |
| POST | `/platform/v1/merge-coordinator/missions/{id}/evaluations` | PR snapshot → decision (`verdict`, `next_gate`, `reasons`) |
| GET | `/platform/v1/merge-coordinator/missions/{id}/decision` | latest decision incl. snapshot, 404 if none |
| GET | `/platform/v1/merge-coordinator/missions/{id}/authorization` | `{authorized, head_sha, decision_id, reasons}` |

PR snapshot fields: `pr_number`, `head_sha`, `head_branch`, `base_branch`, `base_sha`,
`target_sha` (current tip of base), `mergeable`, `conflicted_paths`, `required_checks`,
`checks` (name → conclusion), `unresolved_review_threads`, `cross_repo_conflict`,
`protected_rules_allow`.

## CLI

`record-head`, `record-evidence`, `add-dependency`, `classify-conflicts`,
`merge-evaluate --snapshot-json <json|@file>` (exit 3 when blocked),
`merge-authorization` (exit 3 when not authorized), `serve-merge-api`.
`release` exits 2 with `completion_blocked` when the gate refuses the transition.
