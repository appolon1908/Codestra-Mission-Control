import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace

from mission_control import watchdog_runtime
from mission_control.store import MissionStore


def test_watchdog_runs_dispatcher_and_serializes_slots_snapshot(tmp_path, monkeypatch, capsys):
    store = MissionStore(tmp_path / "watchdog.db")
    store.initialize()

    @dataclass(frozen=True, slots=True)
    class Snapshot:
        tick: int

    scheduler = SimpleNamespace(store=store, tick=lambda: Snapshot(1))
    monkeypatch.setattr(watchdog_runtime, "build_scheduler", lambda **kwargs: scheduler)
    monkeypatch.setattr(sys, "argv", ["watchdog-runtime", "--once"])
    assert watchdog_runtime.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"dispatch_decisions": [], "snapshot": {"tick": 1}}
