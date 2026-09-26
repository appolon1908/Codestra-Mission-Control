from __future__ import annotations

from pathlib import Path

from mission_control.mission_router import MissionRouter
from mission_control.router_catalog import (
    hierarchy_for_repository,
    seed_registered_repositories,
)
from mission_control.store import MissionStore


def config_root() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "router"


def test_default_repository_profile_has_ten_areas():
    hierarchy = hierarchy_for_repository("Codestra-Loki", config_root())
    assert len(hierarchy) == 10
    assert hierarchy[0]["area_id"].startswith("codestra-loki.")
    assert all(area["subareas"] for area in hierarchy)


def test_middleware_uses_specific_profile():
    hierarchy = hierarchy_for_repository("Middleware-", config_root())
    assert len(hierarchy) == 10
    assert hierarchy[0]["area_id"] == "middleware.api-surface"
    assert hierarchy[7]["name"] == "Audit"


def test_seed_registered_repositories_is_idempotent(tmp_path):
    store = MissionStore(tmp_path / "mission.db")
    router = MissionRouter(store)
    router.initialize()
    for repository in ("Middleware-", "Codestra-Loki"):
        store.upsert_repository(
            repository,
            full_name=f"ingtrader21-spec/{repository}",
            local_path=f"/repos/{repository}",
            origin_url=f"https://github.com/ingtrader21-spec/{repository}.git",
            default_branch="main",
            visibility="private",
            local_present=True,
            mission_channel_path=None,
            workspace_path=None,
        )

    first = seed_registered_repositories(router, config_root=config_root())
    second = seed_registered_repositories(router, config_root=config_root())

    assert first == {"Codestra-Loki": 10, "Middleware-": 10}
    assert second == first
    assert len(router.hierarchy("Middleware-")) == 10
    assert len(router.hierarchy("Codestra-Loki")) == 10
