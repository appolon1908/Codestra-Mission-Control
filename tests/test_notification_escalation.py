from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mission_control.models import (
    AgentRole,
    DispatchState,
    Mission,
    NotificationChannel,
    NotificationIncidentState,
)
from mission_control.notification_escalation import (
    EmailEscalationSender,
    NotificationEscalator,
    NotificationPolicy,
    mark_agent_notifications_emitted,
)
from mission_control.store import MissionStore

HEAD_A = "a" * 40
HEAD_B = "b" * 40


def store_for(tmp_path) -> MissionStore:
    store = MissionStore(tmp_path / "mission.db")
    store.initialize()
    store.upsert_mission(
        Mission(
            "PAS-258",
            "repo",
            "notification escalation",
            head_sha=HEAD_A,
        )
    )
    return store


def test_policy_is_two_minute_check_five_minute_retry_three_attempts():
    policy = NotificationPolicy()
    assert policy.check_interval_seconds == 120
    assert policy.retry_interval_seconds == 300
    assert policy.max_attempts == 3


def test_new_dispatch_creates_incident_and_first_notification(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.REVIEWER,
        reason="review exact head",
        head_sha=HEAD_A,
    )
    now = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    tick = NotificationEscalator(store).tick(now=now)

    assert tick.observed == 1
    assert tick.notifications_enqueued == 1
    incidents = store.list_open_notification_incidents()
    assert len(incidents) == 1
    assert incidents[0]["attempt_count"] == 1
    assert incidents[0]["next_notification_at"] == (
        now + timedelta(minutes=5)
    ).isoformat()

    outbox = store.list_notification_outbox()
    assert len(outbox) == 1
    assert outbox[0]["channel"] == NotificationChannel.AGENT.value
    assert outbox[0]["attempt_no"] == 1


def test_three_attempts_then_email_escalation(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.VERIFIER,
        reason="verify exact head",
        head_sha=HEAD_A,
    )
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)

    first = escalator.tick(now=start)
    second = escalator.tick(now=start + timedelta(minutes=5))
    third = escalator.tick(now=start + timedelta(minutes=10))

    assert first.notifications_enqueued == 1
    assert second.notifications_enqueued == 1
    assert third.notifications_enqueued == 1
    assert first.email_escalations_enqueued == 0
    assert second.email_escalations_enqueued == 0
    assert third.email_escalations_enqueued == 1

    incident = store.list_open_notification_incidents()[0]
    assert incident["attempt_count"] == 3
    assert incident["next_notification_at"] is None
    assert incident["email_escalated_at"] is not None

    outbox = store.list_notification_outbox()
    agent_rows = [row for row in outbox if row["channel"] == "AGENT"]
    email_rows = [row for row in outbox if row["channel"] == "EMAIL"]
    assert [row["attempt_no"] for row in agent_rows] == [1, 2, 3]
    assert len(email_rows) == 1
    assert email_rows[0]["attempt_no"] == 3


def test_fourth_tick_does_not_send_more_after_three_attempts(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.REVIEWER,
        reason="review",
        head_sha=HEAD_A,
    )
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    for offset in (0, 5, 10):
        escalator.tick(now=start + timedelta(minutes=offset))

    fourth = escalator.tick(now=start + timedelta(minutes=20))
    assert fourth.notifications_enqueued == 0
    assert fourth.email_escalations_enqueued == 0


def test_ack_stops_retries_and_email(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.REVIEWER,
        reason="review",
        head_sha=HEAD_A,
    )
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    escalator.tick(now=start)
    incident = store.list_open_notification_incidents()[0]

    escalator.acknowledge(int(incident["id"]), actor="ralph")
    tick = escalator.tick(now=start + timedelta(minutes=10))

    assert tick.notifications_enqueued == 0
    assert tick.email_escalations_enqueued == 0
    all_incidents = store.list_notification_incidents()
    assert all_incidents[0]["state"] == NotificationIncidentState.ACKNOWLEDGED.value


def test_dispatch_completion_resolves_incident(tmp_path):
    store = store_for(tmp_path)
    dispatch_id = store.request_dispatch(
        "PAS-258",
        role=AgentRole.REVIEWER,
        reason="review",
        head_sha=HEAD_A,
    )
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    escalator.tick(now=start)

    store.update_dispatch_request(
        dispatch_id,
        state=DispatchState.COMPLETED,
        execution_id="exec-1",
    )
    tick = escalator.tick(now=start + timedelta(minutes=2))
    assert tick.resolved == 1
    incident = store.list_notification_incidents()[0]
    assert incident["state"] == NotificationIncidentState.RESOLVED.value


def test_head_change_stales_old_incident(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.REVIEWER,
        reason="review",
        head_sha=HEAD_A,
    )
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    escalator.tick(now=start)

    store.observe_head(
        "PAS-258",
        head_sha=HEAD_B,
        actor="builder",
    )
    tick = escalator.tick(now=start + timedelta(minutes=2))
    assert tick.stale == 1
    incident = store.list_notification_incidents()[0]
    assert incident["state"] == NotificationIncidentState.STALE.value


def test_agent_notifications_are_emitted_once(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.REVIEWER,
        reason="review",
        head_sha=HEAD_A,
    )
    now = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    NotificationEscalator(store).tick(now=now)
    assert mark_agent_notifications_emitted(store, now=now) == 1
    assert mark_agent_notifications_emitted(store, now=now) == 0


def test_email_without_configuration_remains_pending(tmp_path):
    store = store_for(tmp_path)
    store.request_dispatch(
        "PAS-258",
        role=AgentRole.VERIFIER,
        reason="verify",
        head_sha=HEAD_A,
    )
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    for offset in (0, 5, 10):
        escalator.tick(now=start + timedelta(minutes=offset))

    result = EmailEscalationSender(None).send_due(
        store,
        now=start + timedelta(minutes=10),
    )
    assert result == {"sent": 0, "failed": 0, "blocked_config": 1}
    email_rows = [
        row
        for row in store.list_notification_outbox()
        if row["channel"] == NotificationChannel.EMAIL.value
    ]
    assert len(email_rows) == 1
    assert email_rows[0]["state"] == "PENDING"
