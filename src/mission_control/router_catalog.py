from __future__ import annotations

import copy
import json
from pathlib import Path

from .mission_router import MissionRouter


def _load_profile(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("version", 0)) != 1:
        raise ValueError(f"unsupported router profile version: {path}")
    hierarchy = payload.get("hierarchy")
    if not isinstance(hierarchy, list) or not hierarchy:
        raise ValueError(f"router profile has no hierarchy: {path}")
    return payload


def _namespace_default(repository: str, hierarchy: list[dict]) -> list[dict]:
    result = copy.deepcopy(hierarchy)
    prefix = repository.lower().replace(".", "-").replace("_", "-")
    for area in result:
        old_area_id = str(area["area_id"])
        new_area_id = old_area_id.replace("default.", f"{prefix}.", 1)
        area["area_id"] = new_area_id
        for subarea in area.get("subareas") or []:
            subarea["subarea_id"] = str(subarea["subarea_id"]).replace(
                "default.", f"{prefix}.", 1
            )
    return result


def hierarchy_for_repository(repository: str, config_root: str | Path) -> list[dict]:
    root = Path(config_root)
    normalized = repository.lower().rstrip("-")
    if normalized == "middleware":
        return _load_profile(root / "middleware.v1.json")["hierarchy"]
    default = _load_profile(root / "default-repository.v1.json")
    return _namespace_default(repository, default["hierarchy"])


def seed_registered_repositories(
    router: MissionRouter,
    *,
    config_root: str | Path,
) -> dict[str, int]:
    """Ensure every registered repository has a stable router hierarchy.

    Repository-specific profiles override the default ten-area template.
    This operation is idempotent and does not create atomic tasks by guessing scope.
    """
    counts: dict[str, int] = {}
    for row in router.store.list_repositories():
        repository = str(row["repository"])
        hierarchy = hierarchy_for_repository(repository, config_root)
        router.register_hierarchy(repository, hierarchy)
        counts[repository] = len(hierarchy)
    return counts
