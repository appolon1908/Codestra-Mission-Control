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

API (binds 127.0.0.1:8791 by default):

- `GET  /health`
- `POST /platform/v1/inventory/drift/scans` — run and persist a scan (201; 409 while a scan runs)
- `GET  /platform/v1/inventory/drift?kind=&status=&repository=` — latest scan with `report_stale`/`age_seconds`
- `GET  /platform/v1/inventory/drift/repositories/{repository}` — repository + worktree items
- `GET  /platform/v1/inventory/drift/hosts` — host + runtime items
