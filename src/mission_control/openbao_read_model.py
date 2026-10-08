"""Read-only OpenBao engineering overview for the Codestra dashboard.

Only mission registry/reconciler data is returned. This does not query OpenBao
secrets, vault configuration, unseal endpoints, or make release decisions.
"""
from __future__ import annotations

from datetime import UTC, datetime

REPOSITORY = "Codestra-OpenBao"
REPO_URL = "https://github.com/appolon1908/Codestra-OpenBao"


def snapshot(repo_rows: list[dict], pr_summary: dict, tasks: list) -> dict:
    match = next((r for r in repo_rows if r.get("repository") == REPOSITORY), None)
    entries = sorted(
        (t for t in tasks if t.repository == REPOSITORY),
        key=lambda t: (t.area, t.sub_area, t.task_id),
    )
    sections: dict[str, dict] = {}
    for t in entries:
        name = t.area or "Unassigned"
        row = sections.setdefault(name, {"name": name, "total": 0, "certified": 0, "tasks": []})
        row["total"] += 1
        row["certified"] += int(t.certified)
        row["tasks"].append({
            "task_id": t.task_id,
            "subarea": t.sub_area,
            "completion_percent": round(min(100, max(0, t.completion_percent)), 1),
            "certified": bool(t.certified),
        })
    return {
        "repository": REPOSITORY,
        "observed_at": datetime.now(UTC).isoformat(),
        "data_state": "OBSERVED" if match else "NO_REGISTRY_DATA",
        "registry": {
            "sync_state": match.get("sync_state", "UNKNOWN") if match else "UNKNOWN",
            "ci_state": match.get("ci_state", "UNKNOWN") if match else "UNKNOWN",
            "open_prs": match.get("open_prs") if match else None,
            "remote_head_sha": match.get("remote_head_sha") if match else None,
            "last_synced_at": match.get("last_synced_at") if match else None,
            "active_agents": match.get("active_agents") if match else None,
            "certified_tasks": match.get("certified_tasks") if match else None,
            "total_tasks": match.get("total_tasks") if match else None,
        },
        "pr_summary": {
            "total": int(pr_summary.get("total", 0)),
            "ci_green": int(pr_summary.get("ci_green", 0)),
            "merged": int(pr_summary.get("merged", 0)),
            "post_merge_verified": int(pr_summary.get("post_merge_verified", 0)),
        },
        "sections": list(sections.values()),
        "links": {"repository": REPO_URL, "pull_requests": REPO_URL + "/pulls"},
        "runtime_health": "NOT_CHECKED",
        "release_certification": "NOT_CHECKED",
        "mode": "READ_ONLY",
    }
