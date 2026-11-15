#!/usr/bin/env python3
"""Validate the real handler handshake in the host-launched health subprocess."""

from __future__ import annotations

import json


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
        from main import handle_request

        response = handle_request(payload)
        result = response.get("result")
        healthy = (
            response.get("ok") is True
            and isinstance(result, dict)
            and result.get("contract_version") == "extension_host_v2"
        )
    except Exception:
        healthy = False
    print(json.dumps({"status": "healthy" if healthy else "failed"}))
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
