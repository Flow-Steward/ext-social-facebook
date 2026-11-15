"""Public envelope fixtures and deterministic Graph responses; no live provider traffic."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from handlers import transport  # noqa: E402

PAGE = "10001"
OTHER_PAGE = "20002"
POST = PAGE + "_30003"
COMMENT = "30003_40004"
TOKEN = "fixture-page-token"  # pragma: allowlist secret
APP_SECRET = "fixture-app-secret"  # pragma: allowlist secret


def envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": "extension_host_v2",
        "runtime_context": {
            "account_id": "account-a",
            "project_id": "project-a",
            "group_id": None,
            "for_each_id": None,
            "step_id": None,
            "step_execution_id": None,
        },
        **value,
    }


def action(name: str, values: dict[str, Any] | None = None, **config: Any) -> dict[str, Any]:
    return envelope(
        {
            "mode": "action",
            "action": {
                "action_id": name,
                "context": {"account_id": "account-a", "project_id": "project-a"},
                "input": values or {},
                "target": {
                    "connection": {
                        "connection_id": "connection-page-a",
                        "project_id": "project-a",
                        "account_id": "account-a",
                        "connection_type": "facebook_page",
                        "config": {
                            "page_id": PAGE,
                            "app_id": "50005",
                            "graph_api_version": "v27.0",
                            **config,
                        },
                        "secrets": {"access_token": TOKEN, "app_secret": APP_SECRET},
                    }
                },
            },
        }
    )


def comment_change(**overrides: Any) -> dict[str, Any]:
    value = {
        "item": "comment",
        "verb": "add",
        "comment_id": COMMENT,
        "post_id": POST,
        "parent_id": POST,
        "from": {"id": "60006", "name": "A reader"},
        "message": "A useful question",
        "created_time": 1794690000,
    }
    value.update(overrides)
    return {"field": "feed", "value": value}


def delivery(changes: list[dict[str, Any]] | None = None, *, page: str = PAGE) -> dict[str, Any]:
    return {
        "object": "page",
        "entry": [
            {
                "id": page,
                "time": 1794690001,
                "changes": changes if changes is not None else [comment_change()],
            }
        ],
    }


def ingress(body: dict[str, Any] | None = None, **config: Any) -> dict[str, Any]:
    provider = {
        "provider_key": "flowsteward.facebook",
        "project_id": "project-a",
        "connection_id": "connection-anchor",
        "provider_config": {
            "page_id": PAGE,
            "app_id": "50005",
            "graph_api_version": "v27.0",
            **config,
        },
    }
    callback = {
        "authenticated": True,
        "extension_id": "flowsteward.facebook",
        "callback_id": "comments",
        "project_id": "project-a",
        "connection_id": "connection-anchor",
    }
    request = action("ingress_event")
    request["action"]["input"] = {
        "callback": callback,
        "request": {
            "method": "POST",
            "json": body if body is not None else delivery(),
            "provider": provider,
        },
    }
    # The provider config is the host-resolved callback anchor; no target fallback is allowed.
    request["action"]["target"] = {}
    return request


def invoke(request: dict[str, Any]) -> dict[str, Any]:
    return main.handle_request(copy.deepcopy(request))


class FakeGraph:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[Any] = []

    def reply(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.responses.append((status, headers or {}, raw))

    def fail(self, error: Exception) -> None:
        self.responses.append(error)

    def __call__(self, method, url, *, headers, body, timeout, max_bytes):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected provider request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def form_body(self, index: int = -1) -> dict[str, str]:
        request = self.requests[index]
        headers = {key.lower(): value for key, value in request["headers"].items()}
        assert headers["content-type"].split(";", 1)[0] == "application/x-www-form-urlencoded"
        fields = parse_qs(
            request["body"].decode("utf-8"), keep_blank_values=True, strict_parsing=True
        )
        assert all(len(values) == 1 for values in fields.values())
        return {key: values[0] for key, values in fields.items()}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No live network in extension tests")

    monkeypatch.setattr(transport, "_http_exchange", blocked)
    monkeypatch.setattr("socket.create_connection", blocked)


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(transport, "_http_exchange", fake)
    return fake
