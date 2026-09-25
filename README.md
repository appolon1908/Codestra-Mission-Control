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

## Repo sync and publication-auth readiness

Read-only; never fetches, pushes, resets or edits the worktree. GitHub auth
(`gh auth status`) is evaluated separately from self-hosted `codestra-local`
publication, so a logged-out or missing `gh` blocks only GitHub publication.

    python -m mission_control.cli repo-sync-status --repo <path> [--live]
    python -m mission_control.cli publish-readiness --repo <path> --remote codestra-local [--live]
    python -m mission_control.cli auth-readiness
    python -m mission_control.cli --db <db> serve-repo-sync-api --port 8791

`publish-readiness` exits 0 for READY/UP_TO_DATE, 2 for BLOCKED (dirty,
protected branch, detached HEAD, remote newer/diverged, unreachable remote,
GitHub auth unavailable/CLI missing) and 3 when the path is not a checkout.
`--live` compares against `git ls-remote` instead of cached tracking refs.

HTTP API (GET only, repositories addressed by registry name):

- `GET /health`
- `GET /platform/v1/repo-sync/auth`
- `GET /platform/v1/repo-sync/repositories`
- `GET /platform/v1/repo-sync/repositories/{name}[?live=1]`
- `GET /platform/v1/repo-sync/repositories/{name}/readiness?remote=<remote>[&live=1]`
