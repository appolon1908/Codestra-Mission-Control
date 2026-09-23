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
- action approval levels plus exact-SHA review/verification/merge gates;
- persistent conflict records and dependency-aware merge queue;
- stale-approval invalidation whenever a mission head changes;
- persisted Builder/Reviewer/Verifier redispatch requests;
- 2-minute handoff watch, 5-minute reminders, 3-attempt email escalation;
- persistent SQLite event ledger;
- CLI for local automation;
- adapter contract for Codex, Claude and future workers;
- tests for anti-clash and takeover behavior.

## Safety

Mission Control never treats an agent self-report as certification.

Direct numeric merge approval is intentionally disabled. Merge authorization
requires an exact-SHA Reviewer approval, an independent exact-SHA Verifier
approval, green protected checks, current control-plane evidence, satisfied
dependencies, and the deterministic Merge Coordinator gate.

Production effects, production database writes, live billing, live calling,
live SMS/email and destructive Git operations require explicit policy approval.

## Quick start

    python -m mission_control.cli --db .runtime/mission-control.db init
    python -m mission_control.cli --db .runtime/mission-control.db create-mission --mission PAS-29 --repository Codestra-Mission-Control --goal "Build event-driven IDE agent dispatcher"
    python -m mission_control.cli --db .runtime/mission-control.db claim --mission PAS-29 --agent codex-01 --ttl 600
    python -m mission_control.cli --db .runtime/mission-control.db heartbeat --mission PAS-29 --agent codex-01 --ttl 600
