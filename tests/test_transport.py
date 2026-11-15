"""Exercise the real adapter through a fake public SDK opener with sockets blocked."""

from __future__ import annotations

from urllib.error import HTTPError

import pytest
from conftest import TOKEN

from handlers import transport


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body=b'{"id":"10001_3"}'):
        self.body = body
        self.closed = False
        self.sizes = []

    def read(self, size):
        self.sizes.append(size)
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self):
        self.closed = True


def test_real_adapter_uses_sdk_pinned_request_and_closes_bounded_response(monkeypatch):
    response = Response()
    observed = []

    def opener(request, *, timeout_seconds):
        observed.append((request, timeout_seconds))
        return response

    monkeypatch.setattr(transport, "open_pinned_url", opener)
    # Call the real adapter directly; operation fixtures replace only its alias.
    status, headers, body = transport.pinned_exchange(
        "POST",
        "https://graph.facebook.com/v27.0/10001/feed",
        headers={"Authorization": "Bearer " + TOKEN},
        body=b"message=Hello",
        timeout=10,
        max_bytes=100,
    )
    request, timeout = observed[0]
    assert request.full_url == "https://graph.facebook.com/v27.0/10001/feed"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert request.data == b"message=Hello"
    assert 0 < timeout <= 30
    assert status == 200 and body == b'{"id":"10001_3"}'
    assert headers["Content-Type"] == "application/json"
    assert response.closed and response.sizes and max(response.sizes) <= 101


def test_real_adapter_closes_oversize_and_http_error_responses(monkeypatch):
    response = Response(b"x" * 102)
    monkeypatch.setattr(transport, "open_pinned_url", lambda *a, **kw: response)
    with pytest.raises(transport.ProviderFailure) as caught:
        transport.pinned_exchange(
            "GET",
            "https://graph.facebook.com/v27.0/10001/posts",
            headers={},
            body=None,
            timeout=10,
            max_bytes=100,
        )
    assert caught.value.code == "response_too_large"
    assert response.closed

    error_body = Response(b'{"error":{"code":190}}')
    error = HTTPError("https://graph.facebook.com", 401, "Unauthorized", {}, error_body)

    def rejected(*args, **kwargs):
        raise error

    monkeypatch.setattr(transport, "open_pinned_url", rejected)
    status, _, body = transport.pinned_exchange(
        "GET",
        "https://graph.facebook.com/v27.0/10001/posts",
        headers={},
        body=None,
        timeout=10,
        max_bytes=100,
    )
    assert status == 401 and b"190" in body
    assert error_body.closed


def test_real_adapter_refuses_non_graph_origin_before_opener(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Opener must not receive foreign origin")

    monkeypatch.setattr(transport, "open_pinned_url", forbidden)
    with pytest.raises(transport.ProviderFailure):
        transport.pinned_exchange(
            "GET", "https://evil.example/steal", headers={}, body=None, timeout=10, max_bytes=100
        )


def test_structured_form_values_are_json_encoded():
    assert transport.form_fields({"items": ["1", "2"], "enabled": True}) == {
        "items": '["1","2"]',
        "enabled": "true",
    }


def test_real_adapter_deadline_closes_response(monkeypatch):
    response = Response()
    moments = iter([0.0, 11.0])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(transport, "open_pinned_url", lambda *a, **kw: response)
    with pytest.raises(TimeoutError):
        transport.pinned_exchange(
            "GET",
            "https://graph.facebook.com/v27.0/10001/posts",
            headers={},
            body=None,
            timeout=10,
            max_bytes=100,
        )
    assert response.closed and response.sizes == []
