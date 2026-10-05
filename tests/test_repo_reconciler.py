from mission_control.store import MissionStore
from mission_control.repo_reconciler import RepoReconciler, RepoFacts


class Pub:
    def __init__(self):
        self.events = []

    def publish_http(self, e):
        self.events.append(e)
        return True


def test_reconciler_classifies_and_emits_only_changes(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    p = Pub()
    r = RepoReconciler(s, p)
    f = RepoFacts("WhatsApp", "development", "a", "a", "development", False, 0, 0, 6, "GREEN")
    assert r.reconcile(f)["state"] == "SYNCED" and len(p.events) == 1
    assert not r.reconcile(f)["changed"] and len(p.events) == 1
    g = RepoFacts("WhatsApp", "development", "b", "a", "development", False, 0, 1, 6, "GREEN")
    assert r.reconcile(g)["state"] == "REMOTE_AHEAD" and len(p.events) == 2


def test_dirty_and_diverged_fail_visible(tmp_path):
    s = MissionStore(tmp_path / "db")
    s.initialize()
    r = RepoReconciler(s, Pub())
    assert r.classify(RepoFacts("r", "main", dirty=True)) == "DIRTY"
    assert r.classify(RepoFacts("r", "main", ahead=1, behind=2)) == "DIVERGED"
