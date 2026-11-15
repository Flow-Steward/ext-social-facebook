"""Canonical, secret-free domain responses and external-effect failures."""

from __future__ import annotations

from typing import Any


class ProviderFailure(Exception):
    """Safe domain error; never include provider text or credentials."""

    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        self.code = code
        self.uncertain = uncertain
        super().__init__(code)


def success(
    result: dict[str, Any], *, events: list[dict[str, Any]] | None = None, object_id: str = ""
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "result": result,
        "events": events or [],
        "commands": [],
        "reload": [],
        "errors": [],
    }
    if object_id:
        response.update(external_effect_status="succeeded", external_object_id=object_id)
    return response


def failure(error: ProviderFailure, *, write: bool = False) -> dict[str, Any]:
    message = {
        "not_configured": "Select a configured Facebook Page connection.",
        "foreign_object": "The object does not belong to the selected Page.",
        "ambiguous_external_effect": "The write outcome is unknown. Inspect the provider before retrying.",
    }.get(error.code, "Facebook request failed: " + error.code.replace("_", " ") + ".")
    response: dict[str, Any] = {
        "ok": False,
        "error_code": error.code,
        "error": message,
        "result": {},
        "events": [],
        "commands": [],
        "reload": [],
        "errors": [{"code": error.code, "message": message}],
    }
    if write:
        response["external_effect_status"] = "timeout_unknown" if error.uncertain else "failed"
        if not error.uncertain:
            response["definitely_no_external_effect"] = True
    return response
