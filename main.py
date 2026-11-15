#!/usr/bin/env python3
"""Public SDK subprocess adapter for Facebook Page operations and signed callbacks."""

from __future__ import annotations

import json
import sys
from typing import Any

from flowsteward_extension_sdk import (
    connection_from_payload,
    handshake_result,
    runtime_context_from_payload,
)

from handlers.ingress import normalize
from handlers.operations import OPERATIONS, WRITES, configured, execute
from handlers.responses import ProviderFailure, failure, success

MAX_INPUT_BYTES = 2 * 1024 * 1024


def handle_request(payload: dict[str, Any]) -> dict[str, Any]:
    operation = ""
    try:
        context = runtime_context_from_payload(payload)
        mode = payload.get("mode")
        if mode == "handshake":
            return success(handshake_result())
        if mode == "query":
            query = payload.get("query")
            if not isinstance(query, dict) or query.get("query_id") != "provider_health":
                raise ProviderFailure("invalid_payload")
            connection = connection_from_payload(payload)
            try:
                page, _ = configured(connection)
            except ProviderFailure:
                response = failure(ProviderFailure("not_configured"))
                response["result"] = {
                    "status": "not_configured",
                    "configured": False,
                    "webhook_configured": False,
                }
                return response
            return success(
                {
                    "status": "configured",
                    "configured": True,
                    "page_id": page,
                    "webhook_configured": bool(connection and connection.secrets.get("app_secret")),
                }
            )
        action = payload.get("action")
        if mode != "action" or not isinstance(action, dict):
            raise ProviderFailure("invalid_payload")
        raw_operation = action.get("action_id", "")
        if not isinstance(raw_operation, str):
            raise ProviderFailure("invalid_payload")
        operation = raw_operation
        values = action.get("input", {})
        if not isinstance(values, dict) or not isinstance(operation, str):
            raise ProviderFailure("invalid_payload")
        if operation == "ingress_event":
            return normalize(values, context)
        if operation not in OPERATIONS:
            raise ProviderFailure("invalid_payload")
        return execute(operation, values, connection_from_payload(payload))
    except ProviderFailure as error:
        return failure(error, write=operation in WRITES)
    except (ValueError, TypeError, KeyError):
        return failure(ProviderFailure("invalid_payload"), write=operation in WRITES)
    except Exception:
        # An unexpected failure after a write cannot prove absence of an effect.
        write = operation in WRITES
        return failure(
            ProviderFailure(
                "ambiguous_external_effect" if write else "internal_error", uncertain=write
            ),
            write=write,
        )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Oversize input")
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Invalid envelope")
        response = handle_request(payload)
    except (ValueError, TypeError):
        response = failure(ProviderFailure("invalid_payload"))
    print(json.dumps(response))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
