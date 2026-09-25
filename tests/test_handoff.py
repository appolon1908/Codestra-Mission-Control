from pathlib import Path

from mission_control.handoff import NextTask, compile_next_task, render_next_task, write_next_task


def test_compile_prefers_first_blocker_as_immediate_action(tmp_path):
    mission = {
        "mission_id": "PAS-29",
        "repository": "Codestra-Mission-Control",
        "goal": "Continue automatically",
        "head_sha": "abc123",
        "acceptance_json": '["tests pass", "next task written"]',
    }
    checkpoint = {
        "state": "BLOCKED",
        "head_sha": "abc123",
        "blockers_json": '["fix auth", "rerun tests"]',
    }

    task = compile_next_task(mission, checkpoint)
    rendered = render_next_task(task)

    assert task.blockers == ("fix auth", "rerun tests")
    assert "Resolve blocker: fix auth" in rendered
    assert "Exact HEAD: abc123" in rendered


def test_write_next_task_is_atomic_and_replaces_previous_content(tmp_path):
    task = NextTask(
        mission_id="PAS-29",
        repository="Codestra-Mission-Control",
        goal="Continue automatically",
        state="READY",
        head_sha="abc123",
        blockers=(),
        acceptance=("tests pass",),
    )

    target = write_next_task(tmp_path, task)
    assert target == Path(tmp_path) / "NEXT_TASK.md"
    assert target.exists()
    assert not (Path(tmp_path) / ".NEXT_TASK.md.tmp").exists()
    first = target.read_text(encoding="utf-8")
    assert "Advance the next unmet acceptance criterion: tests pass" in first

    updated = NextTask(
        mission_id="PAS-29",
        repository="Codestra-Mission-Control",
        goal="Continue automatically",
        state="BLOCKED",
        head_sha="def456",
        blockers=("clear conflict",),
        acceptance=("tests pass",),
    )
    write_next_task(tmp_path, updated)
    second = target.read_text(encoding="utf-8")
    assert "Resolve blocker: clear conflict" in second
    assert "Exact HEAD: def456" in second
    assert "abc123" not in second
