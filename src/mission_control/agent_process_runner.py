from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    command = [str(value) for value in spec["command"]]
    cwd = Path(spec["cwd"])
    stdout_path = Path(spec["stdout_path"])
    stderr_path = Path(spec["stderr_path"])
    result_path = Path(spec["result_path"])

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    started = datetime.now(UTC).isoformat()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=os.environ.copy(),
            shell=False,
        )
        exit_code = proc.wait()

    payload = {
        "exit_code": exit_code,
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    tmp = result_path.with_suffix(result_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(result_path)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
