from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mission_control.lease import LeaseManager
from mission_control.models import (
    AgentRole,
    Mission,
    MissionStatus,
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

HEAD_A = 'a' * 40
HEAD_B = 'b' * 40


def store_for(tmp_path, status=MissionStatus.IN_REVIEW) -> MissionStore:
    store = MissionStore(tmp_path / 'mission.db')
    store.initialize()
    store.upsert_mission(Mission('PAS-187', 'repo', 'notification escalation', status=status, head_sha=HEAD_A))
    return store


def test_policy_is_two_minute_check_five_minute_retry_three_attempts():
    policy = NotificationPolicy()
    assert policy.check_interval_seconds == 120
    assert policy.retry_interval_seconds == 300
    assert policy.max_attempts == 3


def test_in_review_creates_reviewer_incident_and_first_notification(tmp_path):
    store = store_for(tmp_path)
    now = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    tick = NotificationEscalator(store).tick(now=now)
    assert tick.observed == 1
    assert tick.notifications_enqueued == 1
    incident = store.list_open_notification_incidents()[0]
    assert incident['required_role'] == AgentRole.REVIEWER.value
    assert incident['attempt_count'] == 1
    assert incident['next_notification_at'] == (now + timedelta(minutes=5)).isoformat()


def test_three_attempts_then_email_escalation(tmp_path):
    store = store_for(tmp_path, MissionStatus.STAGING)
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    first = escalator.tick(now=start)
    second = escalator.tick(now=start + timedelta(minutes=5))
    third = escalator.tick(now=start + timedelta(minutes=10))
    assert (first.notifications_enqueued, second.notifications_enqueued, third.notifications_enqueued) == (1, 1, 1)
    assert third.email_escalations_enqueued == 1
    incident = store.list_open_notification_incidents()[0]
    assert incident['required_role'] == AgentRole.VERIFIER.value
    assert incident['attempt_count'] == 3
    assert incident['next_notification_at'] is None
    outbox = store.list_notification_outbox()
    assert len([r for r in outbox if r['channel'] == 'AGENT']) == 3
    assert len([r for r in outbox if r['channel'] == 'EMAIL']) == 1


def test_ack_stops_retries_and_email(tmp_path):
    store = store_for(tmp_path)
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    escalator.tick(now=start)
    incident = store.list_open_notification_incidents()[0]
    escalator.acknowledge(int(incident['id']), actor='operator')
    tick = escalator.tick(now=start + timedelta(minutes=10))
    assert tick.notifications_enqueued == 0
    assert tick.email_escalations_enqueued == 0
    assert store.list_notification_incidents()[0]['state'] == NotificationIncidentState.ACKNOWLEDGED.value


def test_status_change_resolves_incident(tmp_path):
    store = store_for(tmp_path)
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    escalator.tick(now=start)
    store.set_status('PAS-187', MissionStatus.COMPLETE)
    tick = escalator.tick(now=start + timedelta(minutes=2))
    assert tick.resolved == 1
    assert store.list_notification_incidents()[0]['state'] == NotificationIncidentState.RESOLVED.value


def test_head_change_stales_old_incident(tmp_path):
    store = store_for(tmp_path)
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    escalator.tick(now=start)
    store.upsert_mission(Mission('PAS-187', 'repo', 'notification escalation', status=MissionStatus.IN_REVIEW, head_sha=HEAD_B))
    tick = escalator.tick(now=start + timedelta(minutes=2))
    assert tick.stale == 1
    states = {row['state'] for row in store.list_notification_incidents()}
    assert NotificationIncidentState.STALE.value in states


def test_blocked_mission_requires_writer_attention(tmp_path):
    store = store_for(tmp_path, MissionStatus.BLOCKED)
    NotificationEscalator(store).tick(now=datetime(2026, 9, 23, 17, 0, tzinfo=UTC))
    incident = store.list_open_notification_incidents()[0]
    assert incident['required_role'] == AgentRole.WRITER.value


def test_expired_working_lease_requires_writer_takeover(tmp_path):
    store = store_for(tmp_path, MissionStatus.WORKING)
    leases = LeaseManager(store)
    leases.claim('PAS-187', 'builder', ttl_seconds=600)
    with store.connection() as conn:
        conn.execute("UPDATE leases SET expires_at=? WHERE mission_id='PAS-187'", ('2020-01-01T00:00:00+00:00',))
    NotificationEscalator(store).tick(now=datetime(2026, 9, 23, 17, 0, tzinfo=UTC))
    incident = store.list_open_notification_incidents()[0]
    assert incident['required_role'] == AgentRole.WRITER.value
    assert 'expired' in incident['reason']


def test_agent_notifications_are_emitted_once(tmp_path):
    store = store_for(tmp_path)
    now = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    NotificationEscalator(store).tick(now=now)
    assert mark_agent_notifications_emitted(store, now=now) == 1
    assert mark_agent_notifications_emitted(store, now=now) == 0


def test_email_without_configuration_remains_pending(tmp_path):
    store = store_for(tmp_path)
    escalator = NotificationEscalator(store)
    start = datetime(2026, 9, 23, 17, 0, tzinfo=UTC)
    for offset in (0, 5, 10):
        escalator.tick(now=start + timedelta(minutes=offset))
    result = EmailEscalationSender(None).send_due(store, now=start + timedelta(minutes=10))
    assert result == {'sent': 0, 'failed': 0, 'blocked_config': 1}
    email_rows = [r for r in store.list_notification_outbox() if r['channel'] == NotificationChannel.EMAIL.value]
    assert len(email_rows) == 1
    assert email_rows[0]['state'] == 'PENDING'
