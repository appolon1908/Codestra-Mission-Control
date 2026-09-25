from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .adapters.base import AgentAssignment, AgentExecution
from .git_executor import GitWorktreeExecutor
from .lease import LeaseManager
from .models import AgentRole, MissionStatus
from .store import MissionStore


@dataclass(frozen=True)
class WorkerSlot:
    agent_id: str
    provider: str
    enabled: bool = True


@dataclass(frozen=True)
class DispatchOutcome:
    mission_id: str
    agent_id: str
    provider: str
    state: str
    execution_id: str | None = None
    reason: str | None = None
    takeover: bool = False


@dataclass(frozen=True)
class WatchdogSnapshot:
    active_writers: int
    assigned: tuple[DispatchOutcome, ...] = ()
    blocked: tuple[str, ...] = ()
    expired: tuple[str, ...] = ()
    reminders: tuple[str, ...] = ()


class MissionScheduler:
    def __init__(
        self,
        store: MissionStore,
        *,
        workers: tuple[WorkerSlot, ...],
        adapters: dict[str, object],
        worktree_root: str | Path,
        priorities: dict[str, int] | None = None,
        max_parallel_writers: int = 3,
        lease_ttl_seconds: int = 600,
        git: GitWorktreeExecutor | None = None,
    ) -> None:
        if max_parallel_writers < 1 or max_parallel_writers > 3:
            raise ValueError("max_parallel_writers must be between 1 and 3")
        self.store = store
        self.leases = LeaseManager(store)
        self.workers = workers
        self.adapters = adapters
        self.worktree_root = Path(worktree_root)
        self.priorities = priorities or {}
        self.max_parallel_writers = max_parallel_writers
        self.lease_ttl_seconds = lease_ttl_seconds
        self.git = git or GitWorktreeExecutor()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _active_leases(self) -> list[dict]:
        now = self._now()
        active: list[dict] = []
        for mission in self.store.list_missions():
            lease = self.leases.current(mission["mission_id"])
            if lease and datetime.fromisoformat(lease["expires_at"]) > now:
                active.append(lease)
        return active

    def _idle_workers(self) -> list[WorkerSlot]:
        active_agents = {lease["agent_id"] for lease in self._active_leases()}
        return [
            worker
            for worker in self.workers
            if worker.enabled and worker.agent_id not in active_agents
        ]

    def _priority(self, mission_id: str) -> int:
        return int(self.priorities.get(mission_id, 50))

    def _eligible_missions(self) -> list[dict]:
        now = self._now()
        candidates: list[dict] = []
        for row in self.store.list_missions():
            mission = dict(row)
            status = MissionStatus(mission["status"])
            if status in {MissionStatus.READY, MissionStatus.QUEUED}:
                candidates.append(mission)
                continue
            if status is MissionStatus.WORKING:
                lease = self.leases.current(mission["mission_id"])
                if lease and datetime.fromisoformat(lease["expires_at"]) <= now:
                    candidates.append(mission)
        return sorted(
            candidates,
            key=lambda item: (
                self._priority(item["mission_id"]),
                item["updated_at"],
                item["mission_id"],
            ),
        )

    def _repo_base_ref(self, mission: dict, repository: dict) -> str:
        if mission.get("base_sha"):
            return str(mission["base_sha"])
        default_branch = repository.get("default_branch") or "main"
        if repository.get("origin_url"):
            return f"origin/{default_branch}"
        return "HEAD"

    def _prepare_assignment(
        self,
        mission: dict,
        worker: WorkerSlot,
        *,
        takeover: bool,
    ) -> AgentAssignment:
        repository_row = self.store.get_repository(mission["repository"])
        if not repository_row:
            raise RuntimeError(f"repository not registered: {mission['repository']}")
        repository = dict(repository_row)
        local_path = repository.get("local_path")
        if not local_path or not repository.get("local_present"):
            raise RuntimeError(f"repository not local: {mission['repository']}")

        if takeover:
            execution = self.store.latest_agent_execution(mission["mission_id"])
            if not execution:
                raise RuntimeError("expired mission has no execution checkpoint")
            state = self.git.inspect(execution["worktree"])
            if state.dirty_count:
                self.store.set_status(mission["mission_id"], MissionStatus.NEEDS_DECISION)
                self.store.record_checkpoint(
                    mission["mission_id"],
                    worker.agent_id,
                    "TAKEOVER_BLOCKED_DIRTY",
                    head_sha=state.head_sha,
                    dirty_count=state.dirty_count,
                    tests={},
                    blockers=["expired writer left dirty uncheckpointed work"],
                    next_task_requested=False,
                )
                raise RuntimeError("dirty expired worktree requires decision")
            checkpoint = self.store.latest_checkpoint(mission["mission_id"])
            checkpoint_head = (
                checkpoint["head_sha"]
                if checkpoint and checkpoint["head_sha"]
                else state.head_sha
            )
            assignment = self.git.takeover(
                local_path,
                mission_id=mission["mission_id"],
                new_agent_id=worker.agent_id,
                checkpoint_head=checkpoint_head,
                worktree_root=self.worktree_root,
            )
        else:
            assignment = self.git.create(
                local_path,
                mission_id=mission["mission_id"],
                agent_id=worker.agent_id,
                base_ref=self._repo_base_ref(mission, repository),
                worktree_root=self.worktree_root,
            )

        self.store.update_mission_workspace(
            mission["mission_id"],
            branch=assignment.branch,
            worktree=assignment.worktree,
            base_sha=assignment.base_sha,
            head_sha=assignment.head_sha,
        )
        acceptance = tuple(json.loads(mission["acceptance_json"] or "[]"))
        return AgentAssignment(
            mission_id=mission["mission_id"],
            agent_id=worker.agent_id,
            repository=mission["repository"],
            worktree=assignment.worktree,
            branch=assignment.branch,
            base_sha=assignment.head_sha,
            goal=mission["goal"],
            acceptance=acceptance,
        )

    def _dispatch(self, mission: dict, worker: WorkerSlot) -> DispatchOutcome:
        existing = self.leases.current(mission["mission_id"])
        takeover = bool(existing)
        lease = self.leases.claim(
            mission["mission_id"],
            worker.agent_id,
            role=AgentRole.WRITER,
            ttl_seconds=self.lease_ttl_seconds,
        )
        try:
            assignment = self._prepare_assignment(mission, worker, takeover=takeover)
            adapter = self.adapters[worker.provider]
            execution: AgentExecution = adapter.dispatch(assignment)  # type: ignore[attr-defined]
            return DispatchOutcome(
                mission["mission_id"],
                worker.agent_id,
                worker.provider,
                execution.state,
                execution.execution_id,
                takeover=lease.takeover,
            )
        except Exception as exc:  # noqa: BLE001
            current = self.leases.current(mission["mission_id"])
            if current and current["agent_id"] == worker.agent_id:
                next_status = (
                    MissionStatus.NEEDS_DECISION
                    if "dirty expired worktree" in str(exc)
                    else MissionStatus.READY
                )
                self.leases.release(
                    mission["mission_id"],
                    worker.agent_id,
                    next_status=next_status,
                )
            return DispatchOutcome(
                mission["mission_id"],
                worker.agent_id,
                worker.provider,
                "NOT_DISPATCHED",
                reason=f"{type(exc).__name__}: {exc}",
                takeover=lease.takeover,
            )

    def _replacement_worker(
        self,
        mission: dict,
        workers: list[WorkerSlot],
    ) -> WorkerSlot | None:
        if not workers:
            return None

        lease = self.leases.current(mission["mission_id"])
        if not lease:
            return workers[0]

        # An expired writer must be replaced by a different agent. Reusing the
        # same stopped worker would turn takeover into a silent retry loop and
        # defeat the watchdog's recovery guarantee.
        for worker in workers:
            if worker.agent_id != lease["agent_id"]:
                return worker
        return None

    def tick(self) -> WatchdogSnapshot:
        active = self._active_leases()
        capacity = max(0, self.max_parallel_writers - len(active))
        workers = self._idle_workers()
        candidates = self._eligible_missions()

        assigned: list[DispatchOutcome] = []
        blocked: list[str] = []
        expired: list[str] = []
        for mission in candidates:
            if len(assigned) >= capacity or not workers:
                break

            lease = self.leases.current(mission["mission_id"])
            if lease:
                expired.append(mission["mission_id"])

            worker = self._replacement_worker(mission, workers)
            if worker is None:
                blocked.append(
                    f"{mission['mission_id']}: expired writer {lease['agent_id']} "
                    "has no different replacement worker available"
                )
                continue

            workers.remove(worker)
            outcome = self._dispatch(mission, worker)
            assigned.append(outcome)
            if outcome.state == "NOT_DISPATCHED":
                blocked.append(f"{outcome.mission_id}: {outcome.reason or 'dispatch failed'}")

        return WatchdogSnapshot(
            active_writers=len(self._active_leases()),
            assigned=tuple(assigned),
            blocked=tuple(blocked),
            expired=tuple(expired),
            reminders=tuple(self.reminders()),
        )

    def reminders(self) -> list[str]:
        reminders: list[str] = []
        now = self._now()
        for row in self.store.list_missions():
            mission = dict(row)
            status = MissionStatus(mission["status"])
            if status in {
                MissionStatus.BLOCKED,
                MissionStatus.NEEDS_DECISION,
                MissionStatus.IN_REVIEW,
                MissionStatus.MERGE_READY,
                MissionStatus.STAGING,
            }:
                reminders.append(f"{mission['mission_id']}: {status.value}")
            lease = self.leases.current(mission["mission_id"])
            if lease and datetime.fromisoformat(lease["expires_at"]) <= now:
                reminders.append(
                    f"{mission['mission_id']}: expired writer {lease['agent_id']}"
                )
        return sorted(set(reminders))

    def daily_report(self) -> dict:
        counts: dict[str, int] = {}
        active: list[str] = []
        blocked: list[str] = []
        review: list[str] = []
        approval_required: list[str] = []

        for row in self.store.list_missions():
            mission_id = str(row["mission_id"])
            status = MissionStatus(row["status"])
            counts[status.value] = counts.get(status.value, 0) + 1

            if status is MissionStatus.WORKING:
                active.append(mission_id)
            elif status is MissionStatus.BLOCKED:
                blocked.append(mission_id)
            elif status is MissionStatus.IN_REVIEW:
                review.append(mission_id)

            if status in {
                MissionStatus.NEEDS_DECISION,
                MissionStatus.MERGE_READY,
                MissionStatus.STAGING,
            }:
                approval_required.append(mission_id)

        return {
            "generated_at": self._now().isoformat(),
            "status_counts": counts,
            "active_writers": len(self._active_leases()),
            "max_parallel_writers": self.max_parallel_writers,
            "active": sorted(active),
            "blocked": sorted(blocked),
            "review": sorted(review),
            "approval_required": sorted(approval_required),
            "reminders": self.reminders(),
        }
