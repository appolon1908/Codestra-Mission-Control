from __future__ import annotations

import argparse
import signal
import threading

from .router_api import MissionRouterAPI
from .router_store import RouterStore
from .store import MissionStore


def build(path: str) -> RouterStore:
    base = MissionStore(path)
    base.initialize()
    store = RouterStore(base)
    store.initialize()
    return store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/tmp/agent-brain.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    server = MissionRouterAPI(build(args.db)).server(args.host, args.port)
    signal.signal(
        signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start()
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
