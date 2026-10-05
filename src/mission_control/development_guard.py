from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GuardState(StrEnum):
    CLEAR = "CLEAR"
    BLOCKED = "BLOCKED"
    REPLACEMENT_NEEDED = "REPLACEMENT_NEEDED"


@dataclass(frozen=True)
class DevelopmentEvidence:
    task_id: str
    local_head: str
    expected_base_sha: str
    remote_base_sha: str
    branch: str
    upstream: str | None
    dirty_count: int
    headers: dict[str, str] = field(default_factory=dict)
    required_headers: dict[str, str] = field(default_factory=dict)
    ci_required: tuple[str, ...] = ()
    ci_green: tuple[str, ...] = ()
    heartbeat_current: bool = True
    replacement_assigned: bool = False


@dataclass(frozen=True)
class GuardAnswer:
    state: GuardState
    blockers: tuple[str, ...]
    questions: dict[str, str]


class DevelopmentGuard:
    def evaluate(self, evidence: DevelopmentEvidence) -> GuardAnswer:
        blockers: list[str] = []
        if evidence.branch in {"main", "master"}:
            blockers.append("protected_branch")
        if evidence.dirty_count:
            blockers.append("dirty_worktree")
        if not evidence.upstream:
            blockers.append("missing_upstream")
        if evidence.expected_base_sha != evidence.remote_base_sha:
            blockers.append("stale_base_sha")
        for name, expected in evidence.required_headers.items():
            if evidence.headers.get(name) != expected:
                blockers.append(f"wrong_header:{name}")
        missing_ci = sorted(set(evidence.ci_required) - set(evidence.ci_green))
        blockers.extend(f"ci_not_green:{name}" for name in missing_ci)
        state = GuardState.BLOCKED if blockers else GuardState.CLEAR
        if not evidence.heartbeat_current and not evidence.replacement_assigned:
            state = GuardState.REPLACEMENT_NEEDED
            blockers.append("agent_stopped_without_replacement")
        questions = {
            "what_is_being_built": evidence.task_id,
            "is_base_current": "yes"
            if evidence.expected_base_sha == evidence.remote_base_sha
            else "no",
            "is_lane_clean": "yes" if evidence.dirty_count == 0 else "no",
            "are_headers_correct": "yes"
            if not any(x.startswith("wrong_header:") for x in blockers)
            else "no",
            "is_ci_green": "yes" if not missing_ci else "no",
            "is_agent_covered": "yes"
            if evidence.heartbeat_current or evidence.replacement_assigned
            else "no",
            "can_work_continue": "yes" if state is GuardState.CLEAR else "no",
        }
        return GuardAnswer(state, tuple(blockers), questions)
