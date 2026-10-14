from __future__ import annotations

import argparse
import os
import signal
import threading

from .agent_registry import AgentRegistry
from .dashboard_api import DashboardAPI
from .mission_graph import MissionGraphStore
from .oversight import OversightStore
from .router_store import RouterStore
from .store import MissionStore

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def configure_runtime_auth(host: str, *, local_dev_no_auth: bool = False) -> str:
    """Refuse unauthenticated Mission Control except by explicit loopback test opt-in."""
    environment = os.getenv("MISSION_CONTROL_ENV", "").strip().lower()
    selected = os.getenv("MISSION_CONTROL_AUTH_MODE", "").strip().lower()

    if local_dev_no_auth:
        if host not in LOOPBACK_HOSTS or environment not in {"development", "test"}:
            raise RuntimeError(
                "unauthenticated Mission Control is permitted only for explicit loopback development"
            )
        if selected not in {"", "disabled"}:
            raise RuntimeError("local development auth mode conflicts with required authentication")
        os.environ["MISSION_CONTROL_AUTH_MODE"] = "disabled"
        return "disabled"

    if selected not in {"", "required"}:
        raise RuntimeError("Mission Control runtime requires Keycloak authentication")
    if not os.getenv("MISSION_CONTROL_JWT_ISSUER", "").strip():
        raise RuntimeError("MISSION_CONTROL_JWT_ISSUER is required for authenticated runtime")
    os.environ["MISSION_CONTROL_AUTH_MODE"] = "required"
    return "required"


def build(path: str) -> MissionStore:
    store = MissionStore(path)
    store.initialize()
    AgentRegistry(store).initialize()
    OversightStore(store).initialize()
    MissionGraphStore(store).initialize()
    RouterStore(store).initialize()
    return store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/tmp/agent-brain.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument(
        "--local-dev-no-auth",
        action="store_true",
        help="Explicit unauthenticated loopback-only test mode; requires MISSION_CONTROL_ENV=development or test",
    )
    args = parser.parse_args()
    configure_runtime_auth(args.host, local_dev_no_auth=args.local_dev_no_auth)
    server = DashboardAPI(build(args.db)).server(args.host, args.port)
    signal.signal(
        signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start()
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
