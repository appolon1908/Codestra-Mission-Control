from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .lease import LeaseManager
from .models import (
    AgentRole,
    ApprovalGate,
    ConflictClass,
    MergeQueueState,
    MissionStatus,
)
from .store import MissionStore


@dataclass(frozen=True)
class MergeCandidate:
    mission_id: str
    repository: str
    pr_number: int | None
    head_sha: str
    base_sha: str | None
    target_sha: str | None
    mergeable: bool
    ci_green: bool
    protected_rules_allow: bool
    base_current: bool
    unresolved_review_blockers: bool = False
    control_sync_current: bool = True
    conflict_class: ConflictClass = ConflictClass.NONE
    conflict_files: tuple[str, ...] = ()
    conflict_summary: str = ""
    priority: int = 50


@dataclass(frozen=True)
class MergeDecision:
    mission_id: str
    allowed: bool
    state: MergeQueueState
    reason: str
    required_role: AgentRole | None = None
    invalidated_approvals: int = 0


class MergeCoordinator:
    """Deterministic merge authority for exact-SHA reviewed/verified changes.

    The coordinator never edits product code. It persists approvals, conflicts,
    merge queue state, dependency state, and redispatch requests. An external
    GitHub adapter performs the actual merge only after evaluate returns a
    READY decision.
    """

    def __init__(self, store: MissionStore) -> None:
        self.store = store
        self.leases = LeaseManager(store)

    def _writer_actor(self, mission_id: str) -> str | None:
        lease = self.leases.current(mission_id)
        if lease and lease["role"] == AgentRole.WRITER.value:
            return str(lease["agent_id"])
        return None

    def record_review_acceptance(
        self,
        mission_id: str,
        *,
        actor: str,
        head_sha: str,
        base_sha: str | None = None,
        evidence: dict | None = None,
    ) -> int:
        writer = self._writer_actor(mission_id)
        if writer and actor == writer:
            raise ValueError("writer cannot review its own exact head")
        return self.store.record_sha_approval(
            mission_id,
            gate=ApprovalGate.REVIEW,
            actor=actor,
            actor_role=AgentRole.REVIEWER,
            head_sha=head_sha,
            base_sha=base_sha,
            evidence=evidence,
        )

    def record_verification_acceptance(
        self,
        mission_id: str,
        *,
        actor: str,
        head_sha: str,
        base_sha: str | None = None,
        evidence: dict | None = None,
    ) -> int:
        writer = self._writer_actor(mission_id)
        if writer and actor == writer:
            raise ValueError("writer cannot verify its own exact head")
        review = self.store.latest_valid_sha_approval(
            mission_id,
            ApprovalGate.REVIEW,
            head_sha,
        )
        if review and review["actor"] == actor:
            raise ValueError("reviewer and verifier must be independent actors")
        return self.store.record_sha_approval(
            mission_id,
            gate=ApprovalGate.VERIFICATION,
            actor=actor,
            actor_role=AgentRole.VERIFIER,
            head_sha=head_sha,
            base_sha=base_sha,
            evidence=evidence,
        )

    @staticmethod
    def classify_conflict(
        files: Iterable[str],
        *,
        cross_repo: bool = False,
        mechanical_hint: bool = False,
        semantic_hint: bool = False,
    ) -> ConflictClass:
        paths = tuple(str(path) for path in files)
        if not paths:
            return ConflictClass.NONE
        if cross_repo:
            return ConflictClass.CROSS_REPO
        if semantic_hint:
            return ConflictClass.SEMANTIC
        if mechanical_hint:
            return ConflictClass.MECHANICAL
        return ConflictClass.UNKNOWN

    def _block(
        self,
        candidate: MergeCandidate,
        *,
        state: MergeQueueState,
        reason: str,
        role: AgentRole | None = None,
        invalidated: int = 0,
    ) -> MergeDecision:
        self.store.set_merge_queue_state(candidate.mission_id, state, reason=reason)
        if role is not None:
            self.store.request_dispatch(
                candidate.mission_id,
                role=role,
                reason=reason,
                head_sha=candidate.head_sha,
            )
        return MergeDecision(
            candidate.mission_id,
            False,
            state,
            reason,
            role,
            invalidated,
        )

    def evaluate(
        self,
        candidate: MergeCandidate,
        *,
        coordinator_actor: str = "merge-coordinator",
    ) -> MergeDecision:
        mission = self.store.get_mission(candidate.mission_id)
        if not mission:
            raise KeyError(candidate.mission_id)

        invalidated = self.store.observe_head(
            candidate.mission_id,
            head_sha=candidate.head_sha,
            base_sha=candidate.base_sha,
            actor=coordinator_actor,
        )
        self.store.enqueue_merge(
            candidate.mission_id,
            repository=candidate.repository,
            pr_number=candidate.pr_number,
            head_sha=candidate.head_sha,
            base_sha=candidate.base_sha,
            target_sha=candidate.target_sha,
            priority=candidate.priority,
            state=MergeQueueState.QUEUED,
        )

        open_conflict = self.store.latest_open_conflict(candidate.mission_id)
        if (
            open_conflict
            and candidate.conflict_class is ConflictClass.NONE
        ):
            self.store.set_status(candidate.mission_id, MissionStatus.CONFLICT)
            open_class = ConflictClass(open_conflict["conflict_class"])
            role = (
                AgentRole.WRITER
                if open_class
                in {
                    ConflictClass.MECHANICAL,
                    ConflictClass.SEMANTIC,
                    ConflictClass.CROSS_REPO,
                }
                else None
            )
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_CONFLICT,
                reason=(
                    f"unresolved conflict record {open_conflict['id']} "
                    f"({open_conflict['conflict_class']})"
                ),
                role=role,
                invalidated=invalidated,
            )

        if candidate.conflict_class is not ConflictClass.NONE:
            conflict = self.store.latest_open_conflict(candidate.mission_id)
            if (
                not conflict
                or conflict["head_sha"] != candidate.head_sha
                or conflict["conflict_class"] != candidate.conflict_class.value
            ):
                self.store.record_conflict(
                    candidate.mission_id,
                    repository=candidate.repository,
                    pr_number=candidate.pr_number,
                    head_sha=candidate.head_sha,
                    base_sha=candidate.base_sha,
                    conflict_class=candidate.conflict_class,
                    files=list(candidate.conflict_files),
                    summary=candidate.conflict_summary
                    or f"{candidate.conflict_class.value} merge conflict",
                )
            self.store.set_status(candidate.mission_id, MissionStatus.CONFLICT)
            role = (
                AgentRole.WRITER
                if candidate.conflict_class
                in {
                    ConflictClass.MECHANICAL,
                    ConflictClass.SEMANTIC,
                    ConflictClass.CROSS_REPO,
                }
                else None
            )
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_CONFLICT,
                reason=f"merge conflict {candidate.conflict_class.value}",
                role=role,
                invalidated=invalidated,
            )

        if not candidate.mergeable:
            self.store.set_status(candidate.mission_id, MissionStatus.CONFLICT)
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_CONFLICT,
                reason="GitHub reports PR not mergeable",
                role=AgentRole.WRITER,
                invalidated=invalidated,
            )

        if not candidate.ci_green:
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_CI,
                reason="required CI/checks are not green",
                role=AgentRole.WRITER,
                invalidated=invalidated,
            )

        if candidate.unresolved_review_blockers:
            return self._block(
                candidate,
                state=MergeQueueState.WAITING_REVIEW,
                reason="unresolved blocking review comments remain",
                role=AgentRole.REVIEWER,
                invalidated=invalidated,
            )

        if not candidate.base_current:
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_POLICY,
                reason="target/base moved; refresh branch and invalidate stale evidence",
                role=AgentRole.WRITER,
                invalidated=invalidated,
            )

        if not candidate.protected_rules_allow:
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_POLICY,
                reason="protected GitHub rules do not permit merge",
                invalidated=invalidated,
            )

        if not candidate.control_sync_current:
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_POLICY,
                reason="Linear/Notion/Mission Control checkpoint is not current",
                invalidated=invalidated,
            )

        review = self.store.latest_valid_sha_approval(
            candidate.mission_id,
            ApprovalGate.REVIEW,
            candidate.head_sha,
        )
        if not review:
            self.store.set_status(candidate.mission_id, MissionStatus.IN_REVIEW)
            return self._block(
                candidate,
                state=MergeQueueState.WAITING_REVIEW,
                reason=f"exact head {candidate.head_sha} has no independent review approval",
                role=AgentRole.REVIEWER,
                invalidated=invalidated,
            )

        verification = self.store.latest_valid_sha_approval(
            candidate.mission_id,
            ApprovalGate.VERIFICATION,
            candidate.head_sha,
        )
        if not verification:
            self.store.set_status(candidate.mission_id, MissionStatus.VERIFYING)
            return self._block(
                candidate,
                state=MergeQueueState.WAITING_VERIFICATION,
                reason=f"exact head {candidate.head_sha} has no independent verification approval",
                role=AgentRole.VERIFIER,
                invalidated=invalidated,
            )

        if review["actor"] == verification["actor"]:
            self.store.set_status(candidate.mission_id, MissionStatus.NEEDS_DECISION)
            return self._block(
                candidate,
                state=MergeQueueState.BLOCKED_POLICY,
                reason="review and verification must be performed by independent actors",
                role=AgentRole.VERIFIER,
                invalidated=invalidated,
            )

        if not self.store.merge_dependencies_satisfied(candidate.mission_id):
            self.store.set_status(candidate.mission_id, MissionStatus.WAITING)
            return self._block(
                candidate,
                state=MergeQueueState.WAITING_DEPENDENCY,
                reason="merge dependency is not yet satisfied",
                invalidated=invalidated,
            )

        self.store.record_sha_approval(
            candidate.mission_id,
            gate=ApprovalGate.MERGE_AUTHORIZATION,
            actor=coordinator_actor,
            actor_role=AgentRole.MERGE_COORDINATOR,
            head_sha=candidate.head_sha,
            base_sha=candidate.base_sha,
            evidence={
                "review_actor": review["actor"],
                "verification_actor": verification["actor"],
                "target_sha": candidate.target_sha,
                "ci_green": candidate.ci_green,
                "protected_rules_allow": candidate.protected_rules_allow,
                "control_sync_current": candidate.control_sync_current,
            },
        )
        self.store.set_merge_queue_state(
            candidate.mission_id,
            MergeQueueState.READY,
            reason="all exact-SHA merge gates satisfied",
            actor=coordinator_actor,
        )
        self.store.set_status(candidate.mission_id, MissionStatus.MERGE_READY)
        return MergeDecision(
            candidate.mission_id,
            True,
            MergeQueueState.READY,
            "all exact-SHA merge gates satisfied",
            None,
            invalidated,
        )

    def record_merge(
        self,
        mission_id: str,
        *,
        merge_sha: str,
        merge_method: str,
        actor: str = "merge-coordinator",
    ) -> int:
        return self.store.record_merge_result(
            mission_id,
            merge_sha=merge_sha,
            merge_method=merge_method,
            actor=actor,
        )
