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

All must hold: independently verified registered routing and genuine controller authority on the exact binding; valid SHAs; head branch is not protected and differs from base; PR branch
matches mission branch (when set); PR head == recorded mission head; latest checkpoint is
on that head with `dirty_count == 0`; `base_sha == target_sha` (base is current);
`mergeable == true` with no conflict paths; accepted, blocker-free, independent Reviewer and
Verifier evidence on the head; no unresolved review threads; required checks declared and
all `success`; `protected_rules_allow == true`; every dependency mission is
`COMPLETE`/`CERTIFIED`/`STAGING`.

## Registered routing and controller authority

`ROUTING` and `CONTROLLER` are additive gates. Without an accepted internal provider,
evaluation returns `ROUTING_UNVERIFIED` and `CONTROLLER_VERIFICATION_UNCONFIGURED`.
The standard HTTP/CLI composition supplies no provider, so caller-reported green
checks, actor strings, legacy PASS rows and JSON fields cannot make it merge-ready.

`MissionStore(..., controller_gate_provider=...)` is a privileged Python integration
port, with no JSON, environment or CLI activation path. The existing shared-controller
owner must supply an independently accepted **read-only** implementation. On every
`verify(binding)` call, it must authenticate current operator authority and reacquire
registered routing, genuine assignment/owned lease, accepted dependencies, current
source/base refs and SHAs, producer-bound CI, eligible current-head independent review,
and the independent scope certificate with its issuer provenance and revocation state.
It must verify the registered product/repository mapping and actual repository/PR IDs;
Mission Control's local registration alone is not controller authority.
Do not call the publisher's status-writing path or the legacy certification CLI.

The request binds mission, registered product and canonical repository, PR number,
source/target refs, and head/base/current-target SHAs. A typed observation is only an
internal result, not authentication. It must match that entire binding, contain strict
boolean verdicts and aware timestamps, and satisfy
`observed_at <= now < expires_at <= observed_at + 5 minutes` after provider I/O.
Provider failures, malformed/stale results or missing canonical registration block.
No runtime provider, credential, trust anchor, lease or certificate is created here.
Synthetic test providers validate the consumer boundary only.

The binding is saved with the decision. Authorization reacquires the provider on each
call and checks for local head, decision, evidence or repository changes during the
read. Previously saved decisions without a binding must be evaluated again; they are
never upgraded from a historical verdict. Provider loss or revocation blocks merge,
completion and lease release even when the stored verdict says `MERGE_READY`.
GET `decision` retains its historical verdict/reasons but reports current
`merge_allowed` and `authorization_reasons`. Re-evaluation after gate loss returns the
mission from `MERGE_READY` to `IN_REVIEW`.

These are Mission Control merge gates. Passing local tests does not publish either
GitHub controller context, create governed refs, change branch protection or certify
staging/production. Controller authentication, custody, TLS acceptance and independent
issuer provisioning remain with the existing controller owner.
The workflow retains full Ruff/pytest and removes its historical pytest-success aliases
under the two controller names. Required controller checks remain required and pending
until genuine controller publication; ordinary CI cannot satisfy them.

## Conflict classes

| Class | Meaning | Next gate |
| --- | --- | --- |
| CLASS-0 NONE | clean | NONE |
| CLASS-1 MECHANICAL | lockfiles, generated artifacts, docs | BUILDER_RESOLVE |
| CLASS-2 SEMANTIC | source, config, schema, migration, auth, API spec | BUILDER_RESOLVE |
| CLASS-3 CROSS_REPO | cross-repository contract conflict | CONVERGENCE |
| CLASS-4 UNSAFE | protected surfaces (`.github/`, CODEOWNERS), unknown files, unsafe paths, unknown mergeability, inconsistent evidence | HUMAN_DECISION |

Gate precedence: HUMAN_DECISION, ROUTING, CONTROLLER, CONVERGENCE, BUILDER_RESOLVE, REVIEW, VERIFY, CI, DEPENDENCIES.

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
