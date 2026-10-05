import pytest

from mission_control.store import MissionStore
from mission_control.work_authority import WorkAuthority, WorkItem, WorkType


def setup(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    w = WorkAuthority(s)
    w.initialize()
    return w


def test_hierarchy_requires_canonical_parent(tmp_path):
    w = setup(tmp_path)
    with pytest.raises(ValueError, match="parent"):
        w.publish(WorkItem("A", "WhatsApp", "M", WorkType.AREA, "API", "missing"))
    w.publish(WorkItem("M", "WhatsApp", "M", WorkType.MISSION, "WhatsApp completion"))
    w.publish(WorkItem("A", "WhatsApp", "M", WorkType.AREA, "API", "M"))
    w.publish(WorkItem("S", "WhatsApp", "M", WorkType.SUBAREA, "AI Drafts", "A"))
    w.publish(WorkItem("D", "WhatsApp", "M", WorkType.DELIVERABLE, "Governed AI Draft API", "S"))
    w.publish(WorkItem("T", "WhatsApp", "M", WorkType.ATOMIC_TASK, "Finish PR 12", "D"))
    assert w.require_work("T")["parent_id"] == "D"


def test_implementation_cannot_start_without_lane_authority(tmp_path):
    w = setup(tmp_path)
    w.publish(WorkItem("T", "r", "m", WorkType.ATOMIC_TASK, "task"))
    for args in (
        {"branch": None, "worktree": "/w", "base_sha": "a"},
        {"branch": "main", "worktree": "/w", "base_sha": "a"},
        {"branch": "mission/t", "worktree": None, "base_sha": "a"},
        {"branch": "mission/t", "worktree": "/w", "base_sha": None},
    ):
        with pytest.raises(ValueError):
            w.validate_implementation_start("T", **args)
    w.validate_implementation_start("T", branch="mission/t", worktree="/w", base_sha="abc")


def test_pr_cannot_bind_to_phantom_work(tmp_path):
    w = setup(tmp_path)
    with pytest.raises(ValueError, match="canonical"):
        w.bind_pr("missing", "r", 12)
