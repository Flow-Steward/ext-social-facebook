#!/usr/bin/env python3
"""Exercise the real subprocess handshake without credentials or provider traffic."""

from __future__ import annotations

import json
import subprocess  # nosec B404 -- Local fixed-argv runtime handshake; no shell or external input.
import sys
from pathlib import Path


def main() -> int:
    payload = {
        "contract_version": "extension_host_v2",
        "mode": "handshake",
        "runtime_context": {
            "account_id": "",
            "project_id": "",
            "group_id": None,
            "for_each_id": None,
            "step_id": None,
            "step_execution_id": None,
        },
    }
    healthy = False
    try:
        process = subprocess.run(  # nosec B603 -- Fixed interpreter/script, resolved package cwd, timeout.
            [sys.executable, "main.py"],
            cwd=Path(__file__).resolve().parent,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        response = json.loads(process.stdout)
        healthy = (
            process.returncode == 0
            and response.get("ok") is True
            and response.get("result", {}).get("contract_version") == "extension_host_v2"
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    print(json.dumps({"status": "healthy" if healthy else "failed"}))
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
