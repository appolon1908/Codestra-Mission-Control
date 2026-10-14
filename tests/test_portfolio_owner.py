import ast
import json
import runpy
from pathlib import Path

from mission_control.store import MissionStore

ROOT = Path(__file__).resolve().parents[1]


def test_portfolio_uses_canonical_owner_for_operational_urls():
    manifest = json.loads((ROOT / "config/portfolio.repositories.json").read_text())
    assert manifest["owner"] == "appolon1908"
    for repo in manifest["repositories"]:
        assert repo["full_name"] == f"appolon1908/{repo['name']}"
        assert repo["clone_url"] == f"https://github.com/appolon1908/{repo['name']}.git"


def test_importer_uses_manifest_owner_and_updates_existing_registry(tmp_path):
    source = (ROOT / "scripts/import_github_portfolio.py").read_text()
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "import_portfolio"
        for node in ast.parse(source).body
    ), "Importer must expose a side-effect-free entry point before loading"
    importer = runpy.run_path(str(ROOT / "scripts/import_github_portfolio.py"))
    manifest = tmp_path / "portfolio.json"
    manifest.write_text(json.dumps({
        "owner": "appolon1908",
        "repositories": [{"name": "Codestra-Mission-Control"}],
    }))
    database = tmp_path / "mission.db"
    assert importer["import_portfolio"](database, manifest) == 1
    store = MissionStore(database)
    with store.connection() as conn:
        conn.execute("UPDATE repository_registry SET full_name='legacy/repo'")
    assert importer["import_portfolio"](database, manifest) == 1
    with store.connection() as conn:
        rows = conn.execute("SELECT repository,full_name FROM repository_registry").fetchall()
    assert [tuple(row) for row in rows] == [
        ("Codestra-Mission-Control", "appolon1908/Codestra-Mission-Control")
    ]
