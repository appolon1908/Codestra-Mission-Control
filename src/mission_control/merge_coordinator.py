"""Level-4 Merge Coordinator: deterministic conflict and merge-approval authority.

Every decision binds to an exact PR head SHA. Reviewer and Verifier evidence recorded
against any other SHA is stale. Anything the coordinator cannot prove is treated as a
blocker (fail closed); it never merges and never weakens protected checks.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import asdict, dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from .models import ApprovalLevel, MissionStatus
from .store import MissionStore

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROTECTED_BRANCHES = frozenset({"main", "master", "staging", "production"})
DEPENDENCY_DONE = frozenset(
    {MissionStatus.COMPLETE.value, MissionStatus.CERTIFIED.value, MissionStatus.STAGING.value}
)
PASSING_CHECK_CONCLUSIONS = frozenset({"success"})
COORDINATOR_ACTOR = "merge-coordinator"


class ConflictClass(IntEnum):
    NONE = 0
    MECHANICAL = 1
    SEMANTIC = 2
    CROSS_REPO = 3
    UNSAFE = 4


class EvidenceRole(StrEnum):
    REVIEWER = "REVIEWER"
    VERIFIER = "VERIFIER"


class EvidenceVerdict(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Verdict(StrEnum):
    MERGE_READY = "MERGE_READY"
    BLOCKED = "BLOCKED"


class Gate(StrEnum):
    NONE = "NONE"
    HUMAN_DECISION = "HUMAN_DECISION"
    CONVERGENCE = "CONVERGENCE"
    BUILDER_RESOLVE = "BUILDER_RESOLVE"
    REVIEW = "REVIEW"
    VERIFY = "VERIFY"
    CI = "CI"
    DEPENDENCIES = "DEPENDENCIES"


# Gate precedence when several blockers apply: the earliest gate must clear first.
GATE_ORDER = (
    Gate.HUMAN_DECISION,
    Gate.CONVERGENCE,
    Gate.BUILDER_RESOLVE,
    Gate.REVIEW,
    Gate.VERIFY,
    Gate.CI,
    Gate.DEPENDENCIES,
)

# Protected-surface files: a conflict here could weaken checks or ownership rules.
UNSAFE_PATTERNS = (
    ".github/*",
    "CODEOWNERS",
    "*/CODEOWNERS",
    ".gitmodules",
)
# Behavior/API/auth/schema/migration surfaces: always semantic, even if generated-looking.
SENSITIVE_PATTERNS = (
    "*/migrations/*",
    "migrations/*",
    "schemas/*",
    "*/schemas/*",
    "*.sql",
    "*.proto",
    "*/auth/*",
    "auth/*",
    "openapi.*",
    "*/openapi.*",
)
# Regenerable artifacts: resolve by regenerating, not by reasoning about behavior.
ARTIFACT_PATTERNS = (
    "*.lock",
    "package-lock.json",
    "*/package-lock.json",
    "pnpm-lock.yaml",
    "*/pnpm-lock.yaml",
    "go.sum",
    "*/go.sum",
    "*/generated/*",
    "generated/*",
    "*.generated.*",
    "*.min.js",
    "*.snap",
    "dist/*",
)
SOURCE_PATTERNS = (
    "*.py",
    "*.pyi",
    "*.ts",
    "*.tsx",
    "*.js",
    "*.jsx",
    "*.mjs",
    "*.cjs",
    "*.go",
    "*.rs",
    "*.java",
    "*.kt",
    "*.rb",
    "*.php",
    "*.cs",
    "*.swift",
    "*.c",
    "*.h",
    "*.cpp",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.ini",
    "*.cfg",
    "*.sh",
    "*.ps1",
)
DOC_PATTERNS = (
    "*.md",
    "*.rst",
    "*.txt",
    "docs/*",
)

# First matching tier wins; anything unmatched is UNSAFE (fail closed).
PATH_TIERS = (
    (UNSAFE_PATTERNS, ConflictClass.UNSAFE),
    (SENSITIVE_PATTERNS, ConflictClass.SEMANTIC),
    (ARTIFACT_PATTERNS, ConflictClass.MECHANICAL),
    (SOURCE_PATTERNS, ConflictClass.SEMANTIC),
    (DOC_PATTERNS, ConflictClass.MECHANICAL),
)


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def classify_path(path: str) -> ConflictClass:
    normalized = path.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or ".." in normalized.split("/")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        return ConflictClass.UNSAFE
    normalized = normalized.removeprefix("./")
    for patterns, conflict_class in PATH_TIERS:
        if _matches(normalized, patterns):
            return conflict_class
    return ConflictClass.UNSAFE


@dataclass(frozen=True)
class ConflictAssessment:
    conflict_class: ConflictClass
    next_gate: Gate
    reason: str
    paths: tuple[tuple[str, ConflictClass], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "conflict_class": int(self.conflict_class),
            "label": f"CLASS-{int(self.conflict_class)} {self.conflict_class.name}",
            "next_gate": self.next_gate.value,
            "reason": self.reason,
            "paths": [
                {"path": path, "conflict_class": int(cls), "label": cls.name}
                for path, cls in self.paths
            ],
        }


_CLASS_GATES = {
    ConflictClass.NONE: Gate.NONE,
    ConflictClass.MECHANICAL: Gate.BUILDER_RESOLVE,
    ConflictClass.SEMANTIC: Gate.BUILDER_RESOLVE,
    ConflictClass.CROSS_REPO: Gate.CONVERGENCE,
    ConflictClass.UNSAFE: Gate.HUMAN_DECISION,
}


def classify_conflicts(
    mergeable: bool | None,
    conflicted_paths: list[str] | tuple[str, ...] = (),
    *,
    cross_repo: bool = False,
) -> ConflictAssessment:
    paths = tuple(sorted({str(p) for p in conflicted_paths}))
    classified = tuple((path, classify_path(path)) for path in paths)

    if mergeable is None:
        cls, reason = ConflictClass.UNSAFE, "MERGEABILITY_UNKNOWN"
    elif mergeable and paths:
        cls, reason = ConflictClass.UNSAFE, "MERGEABLE_WITH_CONFLICT_PATHS"
    elif not mergeable and not paths:
        cls, reason = ConflictClass.UNSAFE, "CONFLICT_WITHOUT_PATH_EVIDENCE"
    else:
        cls = max((c for _, c in classified), default=ConflictClass.NONE)
        if cross_repo and cls < ConflictClass.UNSAFE:
            cls = ConflictClass.CROSS_REPO
        reason = "CLEAN" if cls is ConflictClass.NONE else f"CONFLICT_{cls.name}"
    return ConflictAssessment(cls, _CLASS_GATES[cls], reason, classified)


@dataclass(frozen=True)
class PullRequestSnapshot:
    """Observed PR state, e.g. read from `gh pr view --json ...` at evaluation time."""

    pr_number: int
    head_sha: str
    head_branch: str
    base_branch: str
    base_sha: str
    target_sha: str
    mergeable: bool | None
    conflicted_paths: tuple[str, ...] = ()
    required_checks: tuple[str, ...] = ()
    checks: dict[str, str] = field(default_factory=dict)
    unresolved_review_threads: int = 0
    cross_repo_conflict: bool = False
    protected_rules_allow: bool | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PullRequestSnapshot:
        required = ("pr_number", "head_sha", "head_branch", "base_branch", "base_sha", "target_sha")
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            raise ValueError("snapshot missing fields: " + ", ".join(missing))
        mergeable = payload.get("mergeable")
        if mergeable is not None and not isinstance(mergeable, bool):
            raise TypeError("mergeable must be true, false or null")
        protected = payload.get("protected_rules_allow")
        if protected is not None and not isinstance(protected, bool):
            raise TypeError("protected_rules_allow must be true, false or null")
        checks = payload.get("checks") or {}
        if not isinstance(checks, dict):
            raise TypeError("checks must be an object of name -> conclusion")
        return cls(
            pr_number=int(payload["pr_number"]),
            head_sha=str(payload["head_sha"]).lower(),
            head_branch=str(payload["head_branch"]),
            base_branch=str(payload["base_branch"]),
            base_sha=str(payload["base_sha"]).lower(),
            target_sha=str(payload["target_sha"]).lower(),
            mergeable=mergeable,
            conflicted_paths=tuple(str(p) for p in payload.get("conflicted_paths") or ()),
            required_checks=tuple(str(c) for c in payload.get("required_checks") or ()),
            checks={str(k): str(v).lower() for k, v in checks.items()},
            unresolved_review_threads=int(payload.get("unresolved_review_threads") or 0),
            cross_repo_conflict=bool(payload.get("cross_repo_conflict", False)),
            protected_rules_allow=protected,
        )


@dataclass(frozen=True)
class MergeDecision:
    mission_id: str
    verdict: Verdict
    merge_allowed: bool
    head_sha: str
    next_gate: Gate
    conflict: ConflictAssessment
    reasons: tuple[str, ...]
    reviewed_by: str | None
    verified_by: str | None
    decision_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "decision_id": self.decision_id,
            "verdict": self.verdict.value,
            "merge_allowed": self.merge_allowed,
            "head_sha": self.head_sha,
            "next_gate": self.next_gate.value,
            "conflict": self.conflict.as_dict(),
            "reasons": list(self.reasons),
            "reviewed_by": self.reviewed_by,
            "verified_by": self.verified_by,
        }


class EvidenceRejected(ValueError):
    pass


class MergeCoordinator:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def _mission(self, mission_id: str):
        mission = self.store.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        return mission

    def record_head(self, mission_id: str, head_sha: str, actor: str) -> dict:
        head_sha = head_sha.lower()
        if not SHA_RE.match(head_sha):
            raise ValueError("head_sha must be a full 40-character hex SHA")
        self._mission(mission_id)
        return self.store.record_mission_head(mission_id, head_sha, actor)

    def record_evidence(
        self,
        mission_id: str,
        *,
        role: EvidenceRole,
        head_sha: str,
        actor: str,
        verdict: EvidenceVerdict,
        blockers: list[str] | None = None,
    ) -> int:
        mission = self._mission(mission_id)
        head_sha = head_sha.lower()
        if not mission["head_sha"]:
            raise EvidenceRejected("mission has no recorded head; record head first")
        if head_sha != mission["head_sha"]:
            raise EvidenceRejected(
                f"evidence head {head_sha} does not match mission head {mission['head_sha']}"
            )
        if not actor.strip() or actor == COORDINATOR_ACTOR:
            raise EvidenceRejected("evidence actor must be an independent named agent")
        if actor in self.store.writer_agents(mission_id):
            raise EvidenceRejected(f"{actor} wrote this mission; self-review is not allowed")
        return self.store.record_head_evidence(
            mission_id,
            role=role.value,
            head_sha=head_sha,
            actor=actor,
            verdict=verdict.value,
            blockers=list(blockers or []),
        )

    def _evidence_state(
        self,
        mission_id: str,
        role: EvidenceRole,
        head_sha: str,
        writers: set[str],
    ) -> tuple[str | None, list[str]]:
        rows = self.store.head_evidence(mission_id, role.value)
        if not rows:
            return None, [f"{role.value}_MISSING"]
        current = [row for row in rows if row["head_sha"] == head_sha]
        if not current:
            return None, [f"{role.value}_STALE"]
        latest = current[0]
        if latest["actor"] in writers:
            return None, [f"{role.value}_NOT_INDEPENDENT"]
        if latest["verdict"] != EvidenceVerdict.ACCEPTED.value:
            return None, [f"{role.value}_REJECTED"]
        if json.loads(latest["blockers_json"]):
            return None, [f"{role.value}_BLOCKERS_OPEN"]
        return latest["actor"], []

    def evaluate(self, mission_id: str, snapshot: PullRequestSnapshot) -> MergeDecision:
        mission = self._mission(mission_id)
        blockers: list[tuple[Gate, str]] = []

        for name in ("head_sha", "base_sha", "target_sha"):
            if not SHA_RE.match(getattr(snapshot, name)):
                blockers.append((Gate.HUMAN_DECISION, f"INVALID_{name.upper()}"))

        # Exact branch/head binding.
        if snapshot.head_branch in PROTECTED_BRANCHES:
            blockers.append((Gate.HUMAN_DECISION, "HEAD_BRANCH_PROTECTED"))
        if snapshot.head_branch == snapshot.base_branch:
            blockers.append((Gate.HUMAN_DECISION, "HEAD_EQUALS_BASE_BRANCH"))
        if mission["branch"] and mission["branch"] != snapshot.head_branch:
            blockers.append((Gate.HUMAN_DECISION, "PR_BRANCH_MISMATCH"))
        if not mission["head_sha"]:
            blockers.append((Gate.BUILDER_RESOLVE, "MISSION_HEAD_UNRECORDED"))
        elif mission["head_sha"] != snapshot.head_sha:
            blockers.append((Gate.BUILDER_RESOLVE, "PR_HEAD_MISMATCH"))

        checkpoint = self.store.latest_checkpoint(mission_id)
        if not checkpoint or checkpoint["head_sha"] != snapshot.head_sha:
            blockers.append((Gate.BUILDER_RESOLVE, "CHECKPOINT_STALE"))
        elif checkpoint["dirty_count"] != 0:
            blockers.append((Gate.BUILDER_RESOLVE, "CHECKPOINT_DIRTY"))

        # Target freshness: PR must be built on the current tip of its base branch.
        if snapshot.base_sha != snapshot.target_sha:
            blockers.append((Gate.BUILDER_RESOLVE, "BASE_STALE"))

        conflict = classify_conflicts(
            snapshot.mergeable,
            snapshot.conflicted_paths,
            cross_repo=snapshot.cross_repo_conflict,
        )
        if conflict.conflict_class is not ConflictClass.NONE:
            blockers.append((conflict.next_gate, conflict.reason))

        writers = self.store.writer_agents(mission_id)
        reviewer, review_blockers = self._evidence_state(
            mission_id, EvidenceRole.REVIEWER, snapshot.head_sha, writers
        )
        verifier, verify_blockers = self._evidence_state(
            mission_id, EvidenceRole.VERIFIER, snapshot.head_sha, writers
        )
        blockers.extend((Gate.REVIEW, code) for code in review_blockers)
        blockers.extend((Gate.VERIFY, code) for code in verify_blockers)
        if reviewer and verifier and reviewer == verifier:
            blockers.append((Gate.VERIFY, "VERIFIER_SAME_AS_REVIEWER"))
        if snapshot.unresolved_review_threads > 0:
            blockers.append((Gate.REVIEW, "REVIEW_THREADS_UNRESOLVED"))

        if not snapshot.required_checks:
            blockers.append((Gate.CI, "REQUIRED_CHECKS_UNDECLARED"))
        for check in sorted(snapshot.required_checks):
            conclusion = snapshot.checks.get(check)
            if conclusion is None:
                blockers.append((Gate.CI, f"CHECK_MISSING:{check}"))
            elif conclusion not in PASSING_CHECK_CONCLUSIONS:
                blockers.append((Gate.CI, f"CHECK_NOT_GREEN:{check}={conclusion}"))
        if snapshot.protected_rules_allow is not True:
            blockers.append((Gate.CI, "PROTECTED_RULES_NOT_CONFIRMED"))

        for dependency in self.store.dependencies(mission_id):
            dep = self.store.get_mission(dependency)
            if not dep:
                blockers.append((Gate.DEPENDENCIES, f"DEPENDENCY_UNKNOWN:{dependency}"))
            elif dep["status"] not in DEPENDENCY_DONE:
                blockers.append(
                    (Gate.DEPENDENCIES, f"DEPENDENCY_PENDING:{dependency}={dep['status']}")
                )

        ready = not blockers
        gates = {gate for gate, _ in blockers}
        next_gate = next((gate for gate in GATE_ORDER if gate in gates), Gate.NONE)
        reasons = tuple(sorted(code for _, code in blockers))
        verdict = Verdict.MERGE_READY if ready else Verdict.BLOCKED

        next_status: MissionStatus | None = None
        if ready:
            next_status = MissionStatus.MERGE_READY
        elif mission["status"] == MissionStatus.MERGE_READY.value:
            next_status = MissionStatus.IN_REVIEW

        decision_id = self.store.record_merge_decision(
            mission_id,
            head_sha=snapshot.head_sha,
            verdict=verdict.value,
            conflict_class=int(conflict.conflict_class),
            next_gate=next_gate.value,
            reasons=list(reasons),
            snapshot=asdict(snapshot),
            next_status=next_status,
        )
        if ready:
            self.store.record_approval(mission_id, ApprovalLevel.MERGE, COORDINATOR_ACTOR)

        return MergeDecision(
            mission_id=mission_id,
            verdict=verdict,
            merge_allowed=ready,
            head_sha=snapshot.head_sha,
            next_gate=next_gate,
            conflict=conflict,
            reasons=reasons,
            reviewed_by=reviewer,
            verified_by=verifier,
            decision_id=decision_id,
        )

    def latest_decision(self, mission_id: str) -> dict[str, Any] | None:
        self._mission(mission_id)
        row = self.store.latest_merge_decision(mission_id)
        if not row:
            return None
        return {
            "mission_id": mission_id,
            "decision_id": row["id"],
            "head_sha": row["head_sha"],
            "verdict": row["verdict"],
            "merge_allowed": row["verdict"] == Verdict.MERGE_READY.value,
            "conflict_class": row["conflict_class"],
            "next_gate": row["next_gate"],
            "reasons": json.loads(row["reasons_json"]),
            "snapshot": json.loads(row["snapshot_json"]),
            "created_at": row["created_at"],
        }

    def authorization(self, mission_id: str) -> dict[str, Any]:
        return self.store.merge_authorization(mission_id)
