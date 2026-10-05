from __future__ import annotations

import argparse
import signal
import threading

from .agent_registry import AgentRegistry
from .dashboard_api import DashboardAPI
from .mission_graph import MissionGraphStore
from .oversight import OversightStore
from .router_store import RouterStore
from .store import MissionStore


def build(path):
    s = MissionStore(path)
    s.initialize()
    AgentRegistry(s).initialize()
    OversightStore(s).initialize()
    MissionGraphStore(s).initialize()
    RouterStore(s).initialize()
    return s


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="/tmp/agent-brain.db")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    a = p.parse_args()
    server = DashboardAPI(build(a.db)).server(a.host, a.port)
    signal.signal(
        signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start()
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
