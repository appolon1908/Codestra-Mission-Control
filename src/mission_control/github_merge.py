from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .merge_coordinator import MergeCoordinator
from .models import ApprovalGate, MergeQueueState
from .store import MissionStore


class MergeExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeExecutionResult:
    mission_id: str
    repository: str
    pr_number: int
    head_sha: str
    merge_sha: str
    merge_method: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitHubMergeExecutor:
    """CAS-style GitHub merge executor behind the Merge Coordinator gate."""

    def __init__(
        self,
        store: MissionStore,
        *,
        executable: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.store = store
        self.executable = executable or shutil.which("gh") or "gh"
        self.runner = runner
        self.coordinator = MergeCoordinator(store)

    def merge(
        self,
        mission_id: str,
        *,
        merge_method: str = "squash",
        actor: str = "merge-coordinator",
    ) -> MergeExecutionResult:
        queue = self.store.get_merge_queue_item(mission_id)
        if not queue:
            raise MergeExecutionError("mission is not present in merge queue")
        if queue["state"] != MergeQueueState.READY.value:
            raise MergeExecutionError(
                f"merge queue state is {queue['state']}, not READY"
            )

        head_sha = str(queue["head_sha"])
        authorization = self.store.latest_valid_sha_approval(
            mission_id,
            ApprovalGate.MERGE_AUTHORIZATION,
            head_sha,
        )
        if not authorization:
            raise MergeExecutionError(
                f"exact head {head_sha} has no Merge Coordinator authorization"
            )

        repository = str(queue["repository"])
        pr_number = queue["pr_number"]
        if not pr_number:
            raise MergeExecutionError("merge queue item has no PR number")
        if "/" not in repository:
            raise MergeExecutionError(
                f"repository must be owner/name, got {repository!r}"
            )

        proc = self.runner(
            self.executable,
            "api",
            "-X",
            "PUT",
            f"repos/{repository}/pulls/{int(pr_number)}/merge",
            "-f",
            f"sha={head_sha}",
            "-f",
            f"merge_method={merge_method}",
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            self.store.set_merge_queue_state(
                mission_id,
                MergeQueueState.FAILED,
                reason="GitHub merge returned invalid JSON",
                actor=actor,
            )
            raise MergeExecutionError("GitHub merge returned invalid JSON") from exc

        if proc.returncode != 0 or not payload.get("merged"):
            message = (
                payload.get("message")
                or proc.stderr.strip()
                or "GitHub rejected merge"
            )
            self.store.set_merge_queue_state(
                mission_id,
                MergeQueueState.FAILED,
                reason=str(message),
                actor=actor,
            )
            raise MergeExecutionError(str(message))

        merge_sha = payload.get("sha")
        if not isinstance(merge_sha, str) or not merge_sha:
            self.store.set_merge_queue_state(
                mission_id,
                MergeQueueState.FAILED,
                reason="GitHub merge response omitted merge SHA",
                actor=actor,
            )
            raise MergeExecutionError("GitHub merge response omitted merge SHA")

        self.coordinator.record_merge(
            mission_id,
            merge_sha=merge_sha,
            merge_method=merge_method,
            actor=actor,
        )
        return MergeExecutionResult(
            mission_id=mission_id,
            repository=repository,
            pr_number=int(pr_number),
            head_sha=head_sha,
            merge_sha=merge_sha,
            merge_method=merge_method,
        )
