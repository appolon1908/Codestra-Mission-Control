"""Import the canonical portfolio into an explicitly selected local ledger."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mission_control.store import MissionStore
from mission_control.work_authority import WorkAuthority, WorkItem, WorkType

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "config/portfolio.repositories.json"


def import_portfolio(database: str | Path, manifest_path: str | Path = DEFAULT_MANIFEST) -> int:
    manifest = json.loads(Path(manifest_path).read_text())
    owner = manifest["owner"]
    repositories = manifest["repositories"]
    store = MissionStore(database)
    store.initialize()
    authority = WorkAuthority(store)
    authority.initialize()
    with store.connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS repository_registry (
            repository TEXT PRIMARY KEY, full_name TEXT NOT NULL, source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DISCOVERED',
            mission_state TEXT NOT NULL DEFAULT 'UNPLANNED')""")
        for repository in repositories:
            name = repository["name"]
            connection.execute("""INSERT INTO repository_registry
                (repository, full_name, source, status, mission_state)
                VALUES (?, ?, 'GitHub', 'DISCOVERED', 'UNPLANNED')
                ON CONFLICT(repository) DO UPDATE SET
                    full_name=excluded.full_name, source='GitHub'""",
                (name, f"{owner}/{name}"))
    # Publish after the registry transaction closes: WorkAuthority owns its connection.
    for repository in repositories:
        name = repository["name"]
        mission = "PORTFOLIO-" + name
        authority.publish(WorkItem(
            mission, name, mission, WorkType.MISSION, name + " development portfolio",
            metadata={"discovery": "github", "planning_required": True},
        ))
    return len(repositories)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Destination Mission Control ledger")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    print("registered", import_portfolio(args.db, args.manifest), "repositories")


if __name__ == "__main__":
    main()
