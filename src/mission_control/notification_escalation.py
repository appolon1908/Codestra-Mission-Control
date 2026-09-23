from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from .lease import LeaseManager
from .models import (
    AgentRole,
    MissionStatus,
    NotificationChannel,
    NotificationIncidentState,
    NotificationOutboxState,
)
from .store import MissionStore


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class NotificationPolicy:
    check_interval_seconds: int = 120
    retry_interval_seconds: int = 300
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.check_interval_seconds < 30:
            raise ValueError('check interval must be at least 30 seconds')
        if self.retry_interval_seconds < self.check_interval_seconds:
            raise ValueError('retry interval must be >= check interval')
        if self.max_attempts < 1:
            raise ValueError('max attempts must be at least 1')


@dataclass(frozen=True)
class NotificationTick:
    observed: int
    resolved: int
    stale: int
    notifications_enqueued: int
    email_escalations_enqueued: int


class NotificationEscalator:
    """Persistent reminder/escalation engine based on core Mission Control state.

    MC-06 intentionally observes mission status, writer leases, checkpoints and exact
    mission HEADs. It does not depend on Merge Coordinator redispatch tables.
    """

    def __init__(self, store: MissionStore, *, policy: NotificationPolicy | None = None) -> None:
        self.store = store
        self.leases = LeaseManager(store)
        self.policy = policy or NotificationPolicy()

    @staticmethod
    def incident_key(mission_id: str, role: AgentRole, head_sha: str | None) -> str:
        return f"{mission_id}:{role.value}:{head_sha or 'NO_HEAD'}"

    def _link_for(self, repository: str) -> str | None:
        row = self.store.get_repository(repository)
        if not row:
            return None
        origin = str(row['origin_url'] or '')
        if origin.startswith('https://github.com/'):
            return origin.removesuffix('.git')
        return None

    def _required_handoff(self, mission) -> tuple[AgentRole, str] | None:
        status = MissionStatus(mission['status'])
        if status is MissionStatus.IN_REVIEW:
            return AgentRole.REVIEWER, 'mission awaiting independent review'
        if status in {MissionStatus.MERGE_READY, MissionStatus.STAGING}:
            return AgentRole.VERIFIER, f'mission requires verification at {status.value}'
        if status in {MissionStatus.BLOCKED, MissionStatus.NEEDS_DECISION}:
            return AgentRole.WRITER, f'mission requires writer attention at {status.value}'
        if status is MissionStatus.WORKING:
            lease = self.leases.current(str(mission['mission_id']))
            if lease and datetime.fromisoformat(lease['expires_at']) <= _now():
                return AgentRole.WRITER, f"writer lease expired for {lease['agent_id']}"
        return None

    def reconcile_handoffs(self, *, now: datetime | None = None) -> tuple[int, int, int]:
        current = now or _now()
        now_iso = current.isoformat()
        active_keys: set[str] = set()
        observed = 0

        for mission in self.store.list_missions():
            handoff = self._required_handoff(mission)
            if handoff is None:
                continue
            role, reason = handoff
            checkpoint = self.store.latest_checkpoint(str(mission['mission_id']))
            head_sha = mission['head_sha'] or (checkpoint['head_sha'] if checkpoint else None)
            key = self.incident_key(str(mission['mission_id']), role, head_sha)
            active_keys.add(key)
            self.store.upsert_notification_incident(
                incident_key=key,
                mission_id=str(mission['mission_id']),
                required_role=role,
                head_sha=head_sha,
                reason=reason,
                link=self._link_for(str(mission['repository'])),
                next_notification_at=now_iso,
            )
            observed += 1

        resolved = 0
        stale = 0
        for incident in self.store.list_open_notification_incidents():
            mission = self.store.get_mission(str(incident['mission_id']))
            current_head = mission['head_sha'] if mission else None
            if incident['head_sha'] and current_head and incident['head_sha'] != current_head:
                self.store.set_notification_incident_state(
                    int(incident['id']), NotificationIncidentState.STALE, actor='notification-escalator'
                )
                stale += 1
                continue
            if str(incident['incident_key']) not in active_keys:
                self.store.set_notification_incident_state(
                    int(incident['id']), NotificationIncidentState.RESOLVED, actor='notification-escalator'
                )
                resolved += 1
        return observed, resolved, stale

    def tick(self, *, now: datetime | None = None) -> NotificationTick:
        current = now or _now()
        observed, resolved, stale = self.reconcile_handoffs(now=current)
        notifications = 0
        emails = 0
        for incident in self.store.due_notification_incidents(current.isoformat()):
            attempts = int(incident['attempt_count'])
            if attempts >= self.policy.max_attempts:
                continue
            attempt_no = attempts + 1
            payload = {
                'incident_id': int(incident['id']),
                'mission_id': str(incident['mission_id']),
                'required_role': str(incident['required_role']),
                'head_sha': incident['head_sha'],
                'reason': str(incident['reason']),
                'link': incident['link'],
                'attempt': attempt_no,
                'max_attempts': self.policy.max_attempts,
            }
            self.store.enqueue_notification_outbox(
                incident_id=int(incident['id']),
                channel=NotificationChannel.AGENT,
                attempt_no=attempt_no,
                payload=payload,
                available_at=current.isoformat(),
            )
            notifications += 1
            email_escalated = attempt_no >= self.policy.max_attempts
            if email_escalated:
                email_payload = dict(payload)
                email_payload['escalation'] = 'EMAIL_AFTER_MAX_AGENT_ATTEMPTS'
                self.store.enqueue_notification_outbox(
                    incident_id=int(incident['id']),
                    channel=NotificationChannel.EMAIL,
                    attempt_no=attempt_no,
                    payload=email_payload,
                    available_at=current.isoformat(),
                )
                emails += 1
                next_at = None
            else:
                next_at = (current + timedelta(seconds=self.policy.retry_interval_seconds)).isoformat()
            self.store.mark_notification_attempt(
                int(incident['id']), next_notification_at=next_at, email_escalated=email_escalated
            )
        return NotificationTick(observed, resolved, stale, notifications, emails)

    def acknowledge(self, incident_id: int, *, actor: str) -> None:
        self.store.set_notification_incident_state(
            incident_id, NotificationIncidentState.ACKNOWLEDGED, actor=actor
        )


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    sender: str
    recipient: str
    username: str | None = None
    password: str | None = None
    starttls: bool = True

    @classmethod
    def from_env(cls) -> SMTPConfig | None:
        host = os.environ.get('MISSION_CONTROL_SMTP_HOST')
        recipient = os.environ.get('MISSION_CONTROL_ESCALATION_EMAIL_TO')
        sender = os.environ.get('MISSION_CONTROL_ESCALATION_EMAIL_FROM')
        if not host or not recipient or not sender:
            return None
        return cls(
            host=host,
            port=int(os.environ.get('MISSION_CONTROL_SMTP_PORT', '587')),
            sender=sender,
            recipient=recipient,
            username=os.environ.get('MISSION_CONTROL_SMTP_USERNAME'),
            password=os.environ.get('MISSION_CONTROL_SMTP_PASSWORD'),
            starttls=os.environ.get('MISSION_CONTROL_SMTP_STARTTLS', '1') != '0',
        )


class EmailEscalationSender:
    def __init__(self, config: SMTPConfig | None) -> None:
        self.config = config

    def send_due(self, store: MissionStore, *, now: datetime | None = None) -> dict[str, int]:
        current = now or _now()
        rows = store.due_notification_outbox(
            now_iso=current.isoformat(), channel=NotificationChannel.EMAIL
        )
        sent = failed = blocked = 0
        for row in rows:
            if self.config is None:
                blocked += 1
                continue
            payload = json.loads(row['payload_json'])
            message = EmailMessage()
            message['From'] = self.config.sender
            message['To'] = self.config.recipient
            message['Subject'] = f"Codestra escalation: {row['mission_id']} needs {row['required_role']}"
            message.set_content('\n'.join([
                'Codestra Mission Control escalation', '',
                f"Mission: {row['mission_id']}",
                f"Required role: {row['required_role']}",
                f"Exact head: {row['head_sha'] or '(none)'}",
                f"Reason: {row['reason']}",
                f"Attempt: {payload.get('attempt')}",
                f"Link: {row['link'] or '(not available)'}",
            ]))
            try:
                with smtplib.SMTP(self.config.host, self.config.port, timeout=15) as smtp:
                    if self.config.starttls:
                        smtp.starttls()
                    if self.config.username:
                        smtp.login(self.config.username, self.config.password or '')
                    smtp.send_message(message)
            except Exception as exc:  # noqa: BLE001
                store.set_notification_outbox_state(
                    int(row['id']), NotificationOutboxState.FAILED,
                    error=f'{type(exc).__name__}: {exc}',
                )
                failed += 1
                continue
            store.set_notification_outbox_state(int(row['id']), NotificationOutboxState.SENT)
            sent += 1
        return {'sent': sent, 'failed': failed, 'blocked_config': blocked}


def mark_agent_notifications_emitted(store: MissionStore, *, now: datetime | None = None) -> int:
    current = now or _now()
    rows = store.due_notification_outbox(
        now_iso=current.isoformat(), channel=NotificationChannel.AGENT
    )
    emitted = 0
    for row in rows:
        store.set_notification_outbox_state(int(row['id']), NotificationOutboxState.SENT)
        emitted += 1
    return emitted
