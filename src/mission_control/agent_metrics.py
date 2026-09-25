from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .models import MissionStatus
from .store import MissionStore

GOALPOSTS = {
    MissionStatus.QUEUED.value: ("G0_ASSIGNED", "G1_AUTHORITY_VERIFIED", 5.0),
    MissionStatus.READY.value: ("G1_AUTHORITY_VERIFIED", "G2_CONTRACT_FROZEN", 10.0),
    MissionStatus.WORKING.value: ("G3_IMPLEMENTATION", "G4_LOCAL_TESTS", 35.0),
    MissionStatus.IN_REVIEW.value: ("G6_REVIEW_READY", "G7_REVIEW_ACCEPTED", 65.0),
    MissionStatus.WAITING.value: ("G6_WAITING", "G7_REVIEW_ACCEPTED", 60.0),
    MissionStatus.BLOCKED.value: ("G_BLOCKED", "G_NEXT_UNBLOCKED", 50.0),
    MissionStatus.NEEDS_DECISION.value: ("G_DECISION", "G_NEXT_UNBLOCKED", 50.0),
    MissionStatus.MERGE_READY.value: ("G8_PROTECTED_ACCEPTANCE", "G9_STAGING", 82.0),
    MissionStatus.STAGING.value: ("G9_STAGING", "G10_COMPLETE", 92.0),
    MissionStatus.CERTIFIED.value: ("G9_CERTIFIED", "G10_COMPLETE", 97.0),
    MissionStatus.COMPLETE.value: ("G10_COMPLETE", "G10_COMPLETE", 100.0),
}


def _run_git(worktree: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", worktree, *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _minutes_between(start: str, end: datetime | None = None) -> float:
    started = _parse_timestamp(start)
    finish = end or datetime.now(UTC)
    return max(0.0, (finish - started).total_seconds() / 60.0)


class AgentMetricsCollector:
    def __init__(self, store: MissionStore) -> None:
        self.store = store

    def execution_detail(self, execution_id: str) -> dict[str, object]:
        row = self.store.get_agent_execution(execution_id)
        if not row:
            raise KeyError(execution_id)
        detail = dict(row)
        detail["command"] = json.loads(detail.pop("command_json"))
        launch = self.store.get_agent_launch(execution_id)
        detail["launch"] = json.loads(launch["payload_json"]) if launch else None
        rating = self.store.get_agent_rating(execution_id)
        if rating:
            value = dict(rating)
            value["dimensions"] = json.loads(value.pop("dimensions_json"))
            detail["rating"] = value
        else:
            detail["rating"] = None
        work = self.store.get_agent_work_metrics(execution_id)
        detail["work_metrics"] = dict(work) if work else None
        detail["events"] = [dict(item) for item in self.store.execution_events(execution_id)]
        terminal = str(row["state"]) in {"COMPLETED", "FAILED", "STOPPED", "LOST"}
        runtime_end = _parse_timestamp(row["updated_at"]) if terminal else None
        detail["runtime_minutes"] = round(
            _minutes_between(row["started_at"], runtime_end),
            1,
        )
        return detail

    def publication(self, execution_id: str) -> dict[str, object]:
        row = self.store.get_agent_execution(execution_id)
        if not row:
            raise KeyError(execution_id)
        launch = self.store.get_agent_launch(execution_id)
        launch_payload = json.loads(launch["payload_json"]) if launch else {}
        worktree = str(row["worktree"])
        result: dict[str, object] = {
            "execution_id": execution_id,
            "worktree": worktree,
            "launch_head": launch_payload.get("head_sha"),
        }
        if not Path(worktree).exists():
            return {**result, "git_available": False, "reason": "worktree_missing"}
        rc, current_head = _run_git(worktree, "rev-parse", "HEAD")
        if rc:
            return {**result, "git_available": False, "reason": current_head}
        _, branch = _run_git(worktree, "branch", "--show-current")
        _, status = _run_git(worktree, "status", "--porcelain")
        dirty_files = len(status.splitlines()) if status else 0
        upstream_rc, upstream = _run_git(
            worktree, "rev-parse", "--abbrev-ref", "@{upstream}"
        )
        ahead = behind = None
        remote_head = None
        if upstream_rc == 0 and upstream:
            rc2, counts = _run_git(
                worktree, "rev-list", "--left-right", "--count", f"HEAD...{upstream}"
            )
            if rc2 == 0:
                parts = counts.split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])
            rc3, remote_head_value = _run_git(worktree, "rev-parse", upstream)
            if rc3 == 0:
                remote_head = remote_head_value
        commits_since_launch = 0
        launch_head = launch_payload.get("head_sha")
        if launch_head:
            rc4, count = _run_git(worktree, "rev-list", "--count", f"{launch_head}..HEAD")
            if rc4 == 0 and count.isdigit():
                commits_since_launch = int(count)
        parity = remote_head == current_head if remote_head else None
        return {
            **result,
            "git_available": True,
            "branch": branch,
            "current_head": current_head,
            "upstream": upstream if upstream_rc == 0 else None,
            "remote_head": remote_head,
            "remote_parity": parity,
            "ahead": ahead,
            "behind": behind,
            "dirty_files": dirty_files,
            "commits_since_launch": commits_since_launch,
            "unpushed_commits": ahead or 0,
            "unpushed_files": dirty_files,
        }

    def reliability(self, execution_id: str) -> dict[str, object]:
        events = [dict(row) for row in self.store.execution_events(execution_id)]
        counts: dict[str, int] = {}
        for event in events:
            counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
        return {
            "execution_id": execution_id,
            "stop_count": counts.get("AGENT_STOPPED", 0),
            "failure_count": counts.get("AGENT_FAILED", 0),
            "lost_count": counts.get("AGENT_LOST", 0),
            "launch_count": counts.get("AGENT_LAUNCHED", 0),
            "restart_count": max(0, counts.get("AGENT_LAUNCHED", 0) - 1),
            "event_count": len(events),
        }

    def velocity(self, execution_id: str) -> dict[str, object]:
        work = self.store.get_agent_work_metrics(execution_id)
        if not work:
            return {
                "execution_id": execution_id,
                "effective_velocity_points_per_hour": 0.0,
                "net_velocity_points_per_hour": 0.0,
                "active_hours": 0.0,
            }
        active_hours = float(work["active_minutes"]) / 60.0
        if active_hours <= 0:
            effective = net = 0.0
        else:
            effective = float(work["verified_points"]) / active_hours
            net = (float(work["verified_points"]) - float(work["rework_points"])) / active_hours
        return {
            "execution_id": execution_id,
            "effective_velocity_points_per_hour": round(effective, 2),
            "net_velocity_points_per_hour": round(net, 2),
            "active_hours": round(active_hours, 2),
        }

    def goalposts(self, execution_id: str) -> dict[str, object]:
        row = self.store.get_agent_execution(execution_id)
        if not row:
            raise KeyError(execution_id)
        mission = self.store.get_mission(row["mission_id"])
        if not mission:
            raise KeyError(row["mission_id"])
        current, nxt, default_pct = GOALPOSTS.get(
            mission["status"], ("G_UNKNOWN", "G_UNKNOWN", 0.0)
        )
        work = self.store.get_agent_work_metrics(execution_id)
        if work:
            current = work["current_goalpost"] or current
            nxt = work["next_goalpost"] or nxt
            pct = float(work["mission_completion_pct"])
        else:
            pct = default_pct
        return {
            "execution_id": execution_id,
            "mission_id": row["mission_id"],
            "mission_status": mission["status"],
            "current_goalpost": current,
            "next_goalpost": nxt,
            "mission_completion_pct": round(max(0.0, min(100.0, pct)), 1),
            "remaining_pct": round(max(0.0, 100.0 - pct), 1),
        }

    def refresh(self, execution_id: str) -> dict[str, object]:
        row = self.store.get_agent_execution(execution_id)
        if not row:
            raise KeyError(execution_id)
        publication = self.publication(execution_id)
        reliability = self.reliability(execution_id)
        existing = self.store.get_agent_work_metrics(execution_id)
        payload: dict[str, object] = {
            "execution_id": execution_id,
            "agent_id": row["agent_id"],
            "mission_id": row["mission_id"],
            "produced_points": float(existing["produced_points"]) if existing else 0.0,
            "pushed_points": float(existing["pushed_points"]) if existing else 0.0,
            "verified_points": float(existing["verified_points"]) if existing else 0.0,
            "rework_points": float(existing["rework_points"]) if existing else 0.0,
            "unpushed_commits": int(publication.get("unpushed_commits", 0) or 0),
            "unpushed_files": int(publication.get("unpushed_files", 0) or 0),
            "stop_count": int(reliability["stop_count"]),
            "unexpected_stop_count": int(reliability["lost_count"]),
            "restart_count": int(reliability["restart_count"]),
            "stalled_minutes": float(existing["stalled_minutes"]) if existing else 0.0,
            "blocked_minutes": float(existing["blocked_minutes"]) if existing else 0.0,
            "active_minutes": round(
                _minutes_between(
                    row["started_at"],
                    _parse_timestamp(row["updated_at"])
                    if str(row["state"]) in {"COMPLETED", "FAILED", "STOPPED", "LOST"}
                    else None,
                ),
                1,
            ),
            "current_goalpost": existing["current_goalpost"] if existing else None,
            "next_goalpost": existing["next_goalpost"] if existing else None,
            "mission_completion_pct": float(existing["mission_completion_pct"]) if existing else 0.0,
        }
        self.store.upsert_agent_work_metrics(payload)
        return {
            "work_metrics": payload,
            "publication": publication,
            "reliability": reliability,
            "velocity": self.velocity(execution_id),
            "goalposts": self.goalposts(execution_id),
        }

    def stale(self, *, minutes: float = 30.0) -> list[dict[str, object]]:
        now = datetime.now(UTC)
        results: list[dict[str, object]] = []
        for row in self.store.list_active_agents():
            latest_events = self.store.execution_events(row["execution_id"])
            observed_at = (
                latest_events[-1]["created_at"]
                if latest_events
                else row["created_at"]
            )
            age = (now - _parse_timestamp(observed_at)).total_seconds() / 60.0
            if age >= minutes:
                results.append(
                    {
                        **dict(row),
                        "last_observed_at": observed_at,
                        "age_minutes": round(age, 1),
                    }
                )
        return results

    def leaderboard(self, *, limit: int = 20) -> list[dict[str, object]]:
        rows = [dict(row) for row in self.store.list_agent_ratings(limit=500)]
        rows.sort(key=lambda item: (-float(item["avi"]), -float(item["confidence"])))
        output = []
        for index, row in enumerate(rows[:limit], start=1):
            row["rank"] = index
            row["dimensions"] = json.loads(row.pop("dimensions_json"))
            output.append(row)
        return output
