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

## Worker nodes (CORE-NODE-02)

Worker nodes register durable capabilities (lanes, providers, worktree root,
tools) and report heartbeat plus provider-auth status. Every node must carry the
`builder` lane; review-only nodes are rejected. Payloads containing
secret-bearing keys are rejected, and auth details are redacted before storage.

    python -m mission_control.cli --db .runtime/mission-control.db node-probe --node core-node-02 --lane builder --lane reviewer --lane verifier --worktree-root /home/codestra/Worktrees
    python -m mission_control.cli --db .runtime/mission-control.db node-status --node core-node-02
    python -m mission_control.cli --db .runtime/mission-control.db serve-worker-node-api --port 8791

HTTP API (`127.0.0.1` by default):

- `GET /health`
- `GET /platform/v1/worker-nodes`
- `GET|PUT /platform/v1/worker-nodes/{node_id}`
- `POST /platform/v1/worker-nodes/{node_id}/heartbeat`
- `GET /platform/v1/worker-nodes/{node_id}/readiness`
- `GET /platform/v1/worker-nodes/{node_id}/provider-auth`
- `PUT /platform/v1/worker-nodes/{node_id}/provider-auth/{provider}`

Readiness is `READY`, `DEGRADED` (implementation-ready with warnings) or
`NOT_READY`. Implementation work requires a fresh heartbeat (300s), git, a
writable worktree root, at least one provider authenticated within the last
hour, and production effects disabled.
