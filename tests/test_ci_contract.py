"""CI must reject existing Python debt even on documentation-only changes."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_runs_full_lint_and_real_pytest_for_all_pull_requests():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "  pull_request:\n  push:" in workflow
    assert "  test:\n" in workflow
    assert 'python-version: "3.12"' in workflow
    assert "python -m ruff check src tests" in workflow
    assert "git diff" not in workflow
    assert "python -m pytest -q" in workflow
    assert "continue-on-error" not in workflow
    assert "-c requirements-ci.txt" in workflow


def test_full_lint_rejects_an_unchanged_invalid_module(tmp_path):
    for directory in ("src", "tests"):
        (tmp_path / directory).mkdir()
    module = tmp_path / "src" / "existing.py"
    module.write_text("import definitely_unused\n")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests"],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 1
    assert "F401" in result.stdout


def test_ci_constraints_pin_the_runtime_and_test_dependency_graph():
    pins = dict(
        line.split("==", 1)
        for line in (ROOT / "requirements-ci.txt").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert pins["ruff"] == "0.16.10"
    assert pins["pytest"] == "9.1.1"
    assert {"PyJWT", "cryptography", "setuptools", "wheel", "cffi"} <= pins.keys()
