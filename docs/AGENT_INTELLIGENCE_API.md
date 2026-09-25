# Agent Intelligence API

Mission Control exposes a bounded agent telemetry and evaluation API.

Start locally:

```bash
codestra-mission-control --db .runtime/mission-control.db serve-agent-api --host 127.0.0.1 --port 8766
```

Keep the service private. Bind to loopback by default or to an approved Tailscale/private interface. Do not expose it through the public product edge.

## Endpoints

- `GET /healthz` and `GET /readyz` — service health/readiness.
- `GET /metrics` — Prometheus-format agent launch/active/stop/failure counters.
- `POST/GET /platform/v1/agents/launch-events` — durable launch records.
- `POST/GET /platform/v1/agents/events` — lifecycle events such as STOPPED, FAILED, LOST and COMPLETED.
- `GET /platform/v1/agents/active` — latest-state active executions.
- `POST/GET /platform/v1/agents/ratings` — AVI evaluations computed server-side from scored dimensions.
- `POST/GET /platform/v1/agents/work-metrics` — produced/pushed/verified/rework volume, stops, restarts, stalled/blocked/active time and goalposts.
- `GET /platform/v1/agents/summary` — active/launch/stop/failure aggregate.

## Response headers

JSON responses include `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Codestra-API-Version: v1`, and a per-response `X-Request-Id`.

## Delivery authority

Launch events are emitted automatically by `MissionScheduler` after a successful managed dispatch. Process-backed agents also emit terminal lifecycle events when their state changes. Deliberately stopped executions remain `STOPPED` and are not reclassified as `LOST`.

## Safety

The API records control-plane evidence only. It does not authorize merge, staging, production, provider effects or branch-protection bypass.
