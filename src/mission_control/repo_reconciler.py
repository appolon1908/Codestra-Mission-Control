from __future__ import annotations
from dataclasses import dataclass
from .repository_sync import RepositorySyncStore
from .realtime_events import RealtimeEvent, RealtimePublisher


@dataclass(frozen=True)
class RepoFacts:
    repository: str
    default_branch: str
    remote_head_sha: str | None = None
    local_head_sha: str | None = None
    local_branch: str | None = None
    dirty: bool = False
    ahead: int | None = None
    behind: int | None = None
    open_prs: int = 0
    ci_state: str = "UNKNOWN"


class RepoReconciler:
    def __init__(self, store, publisher=None):
        self.sync = RepositorySyncStore(store)
        self.sync.initialize()
        self.publisher = publisher or RealtimePublisher()

    def classify(self, f: RepoFacts) -> str:
        if f.dirty:
            return "DIRTY"
        if f.ahead and f.behind:
            return "DIVERGED"
        if f.behind:
            return "REMOTE_AHEAD"
        if f.ahead:
            return "LOCAL_AHEAD"
        if f.remote_head_sha and f.local_head_sha and f.remote_head_sha == f.local_head_sha:
            return "SYNCED"
        return "UNKNOWN"

    def reconcile(self, f: RepoFacts) -> dict:
        before = self.sync.snapshot(f.repository)
        state = self.classify(f)
        self.sync.upsert_repo(
            f.repository,
            default_branch=f.default_branch,
            remote_head_sha=f.remote_head_sha,
            local_head_sha=f.local_head_sha,
            local_branch=f.local_branch,
            dirty=f.dirty,
            ahead=f.ahead,
            behind=f.behind,
            open_prs=f.open_prs,
            ci_state=f.ci_state,
            sync_state=state,
        )
        after = self.sync.snapshot(f.repository)
        changed = not before or any(
            before.get(k) != after.get(k)
            for k in (
                "remote_head_sha",
                "local_head_sha",
                "dirty",
                "ahead",
                "behind",
                "open_prs",
                "ci_state",
                "sync_state",
            )
        )
        if changed:
            self.publisher.publish_http(
                RealtimeEvent.create(
                    "repo.state.changed",
                    {
                        "repository": f.repository,
                        "sync_state": state,
                        "open_prs": f.open_prs,
                        "ci_state": f.ci_state,
                    },
                )
            )
        return {"changed": changed, "state": state, "snapshot": after}
