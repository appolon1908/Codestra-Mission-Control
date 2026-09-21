#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mission_control.network_fabric import normalize_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("status_json")
    args = parser.parse_args()
    payload = json.loads(Path(args.status_json).read_text(encoding="utf-8"))
    nodes = normalize_status(payload)
    print(json.dumps([node.__dict__ for node in nodes], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
