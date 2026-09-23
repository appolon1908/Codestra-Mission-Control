# Agent Handoff Notification Escalation

This runtime policy is part of PAS-258 because it is how Mission Control
reliably hands work between Builder, Reviewer, Verifier and Merge Coordinator.

## Timing

- Handoff conditions are evaluated every **120 seconds (2 minutes)**.
- First reminder is emitted on the first evaluation that sees a pending handoff.
- If still unresolved, reminders repeat every **300 seconds (5 minutes)**.
- A maximum of **3 agent notification attempts** is allowed per incident.
- Immediately after the 3rd unsuccessful attempt, Mission Control creates an
  **EMAIL escalation** in the durable notification outbox.
- No 4th agent reminder is emitted for the same incident.

## Incident identity

The incident key is:

    mission_id + required_role + exact_head_sha

This means a new commit creates a new incident. Old-head incidents become
STALE and cannot continue to notify or escalate.

## Stop conditions

The incident stops immediately when:

- the required agent dispatch completes;
- the incident is acknowledged;
- the mission/head changes and invalidates the incident;
- Mission Control resolves the handoff.

## Durable state

Two SQLite tables back the feature:

- notification_incidents
- notification_outbox

Outbox channels are:

- AGENT
- EMAIL

The AGENT channel is the durable reminder/audit surface around the existing
dispatch-request handoff. The EMAIL channel is created only after attempt 3.

## Email configuration

Actual email delivery is configuration-gated. Secrets are never persisted in
Mission Control's database.

Required environment variables:

    MISSION_CONTROL_ESCALATION_EMAIL_TO
    MISSION_CONTROL_ESCALATION_EMAIL_FROM
    MISSION_CONTROL_SMTP_HOST

Optional environment variables:

    MISSION_CONTROL_SMTP_PORT        # default 587
    MISSION_CONTROL_SMTP_USERNAME
    MISSION_CONTROL_SMTP_PASSWORD
    MISSION_CONTROL_SMTP_STARTTLS    # default 1

If email delivery is not configured, the EMAIL outbox entry remains PENDING
instead of being discarded. This preserves the escalation evidence and avoids
pretending an email was sent.

## Single notification authority

Only one authoritative Mission Control watchdog should own notification
delivery. Database uniqueness makes repeated observations idempotent, but
multiple disconnected databases must not independently deliver the same
incident.

## CLI

List open notification incidents:

    python -m mission_control.cli --db .runtime/mission-control.db       notifications --open-only

Acknowledge an incident:

    python -m mission_control.cli --db .runtime/mission-control.db       notification-ack --incident-id 12 --actor operator

Inspect durable notification outbox:

    python -m mission_control.cli --db .runtime/mission-control.db       notification-outbox
