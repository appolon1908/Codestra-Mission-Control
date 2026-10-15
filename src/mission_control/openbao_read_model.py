"""Read-only OpenBao engineering overview for the Codestra dashboard.

Only mission registry/reconciler data is returned. This does not query OpenBao
secrets, vault configuration, unseal endpoints, or make release decisions.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

REPOSITORY = "Codestra-OpenBao"
REPO_URL = "https://github.com/appolon1908/Codestra-OpenBao"


def snapshot(repo_rows: list[dict], pr_summary: dict, tasks: list) -> dict:
    match = next((r for r in repo_rows if r.get("repository") == REPOSITORY), None)
    rows = [dict(t) if isinstance(t, dict) else asdict(t) for t in tasks]
    entries = sorted(
        (t for t in rows if t.get("repository") == REPOSITORY),
        key=lambda t: (t.get("area") or "", t.get("sub_area") or "", t["task_id"]),
    )
    sections: dict[str, dict] = {}
    for t in entries:
        name = t.get("area") or "Unassigned"
        certified = bool(t.get("certified", t.get("status") == "CERTIFIED"))
        row = sections.setdefault(name, {"name": name, "total": 0, "certified": 0, "tasks": []})
        row["total"] += 1
        row["certified"] += int(certified)
        row["tasks"].append({
            "task_id": t["task_id"],
            "subarea": t.get("sub_area"),
            "completion_percent": round(min(100, max(0, t.get("completion_percent", 0))), 1),
            "certified": certified,
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
            key: int(pr_summary[key]) if pr_summary.get(key) is not None else None
            for key in ("total", "ci_green", "merged", "post_merge_verified")
        },
        "sections": list(sections.values()),
        "links": {"repository": REPO_URL, "pull_requests": REPO_URL + "/pulls"},
        "runtime_health": "NOT_CHECKED",
        "release_certification": "NOT_CHECKED",
        "mode": "READ_ONLY",
    }
