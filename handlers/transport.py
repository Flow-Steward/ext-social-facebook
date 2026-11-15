"""Fixed-origin, bounded Graph transport using the public SDK's peer-pinned opener."""

from __future__ import annotations

import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from flowsteward_extension_sdk import Request, open_pinned_url

from handlers.responses import ProviderFailure, WriteBoundary

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10.0


def pinned_exchange(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    """One request, no redirects/retries; always close normal and HTTP-error streams."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.facebook.com"
        or parsed.fragment
        or method not in {"GET", "POST"}
        or not math.isfinite(timeout)
        or not 0 < timeout <= 30
        or not 0 < max_bytes <= MAX_BYTES
    ):
        raise ProviderFailure("invalid_payload")
    deadline = time.monotonic() + timeout
    try:
        response = open_pinned_url(
            Request(url, data=body, headers=headers, method=method), timeout_seconds=timeout
        )
    except HTTPError as error:
        response = error
    try:
        chunks = []
        size = 0
        # read1 performs one underlying read when supported, avoiding an unbounded
        # drip-fed read(size). Each socket wait and the whole loop have a deadline.
        reader = getattr(response, "read1", None) or response.read
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Graph response deadline exceeded")
            chunk = reader(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise ProviderFailure("response_too_large")
        return (
            int(response.status if response.status is not None else response.code),
            dict(response.headers),
            b"".join(chunks),
        )
    finally:
        response.close()


_http_exchange = pinned_exchange


def form_fields(values: dict[str, Any]) -> dict[str, str]:
    """Match Meta's form encoding for structured field values."""
    return {
        key: json.dumps(value, separators=(",", ":"))
        if isinstance(value, (dict, list, bool))
        else str(value)
        for key, value in values.items()
    }


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(nested, secrets)
            for key, nested in value.items()
            if key.lower() not in {"access_token", "app_secret", "paging", "next"}
        }
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


class GraphClient:
    """One selected Page's Graph client; a write is never automatically retried."""

    def __init__(  # nosec B107 -- Empty optional webhook secret means unset, never a usable credential.
        self,
        version: str,
        token: str,
        app_secret: str = "",
        *,
        boundary: WriteBoundary | None = None,
    ) -> None:
        self.version = version
        self.token = token
        self.app_secret = app_secret
        self.boundary = boundary if boundary is not None else WriteBoundary()

    def request(self, method: str, path: str, values: dict[str, Any]) -> dict[str, Any]:
        url = f"https://graph.facebook.com/{self.version}/{path}"
        fields = form_fields(values)
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        body = None
        if method == "GET" and fields:
            url += "?" + urlencode(fields)
        elif method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields).encode("utf-8")
        try:
            if method == "POST":
                # Set before crossing the exchange seam: any later parsing or
                # mapping exception must preserve uncertainty about remote effects.
                self.boundary.attempts += 1
            status, _, raw = _http_exchange(
                method,
                url,
                headers=headers,
                body=body,
                timeout=TIMEOUT_SECONDS,
                max_bytes=MAX_BYTES,
            )
            if len(raw) > MAX_BYTES:
                raise ProviderFailure("response_too_large")
            if 300 <= status < 400:
                raise ProviderFailure("provider_unavailable", uncertain=method == "POST")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError) as error:
                raise ProviderFailure("provider_unavailable", uncertain=method == "POST") from error
            if not isinstance(result, dict):
                raise ProviderFailure("provider_unavailable", uncertain=method == "POST")
            if status >= 400 or "error" in result:
                detail = result.get("error")
                code = detail.get("code") if isinstance(detail, dict) else None
                if type(code) is not int:
                    raise ProviderFailure("provider_unavailable", uncertain=method == "POST")
                if status == 401 or code == 190:
                    failure = "authentication_failed"
                elif status == 403 or code in {10, 200}:
                    failure = "permission_denied"
                elif status == 429 or code in {4, 17, 32, 613}:
                    failure = "rate_limited"
                elif status == 404:
                    failure = "not_found"
                else:
                    failure = "provider_unavailable"
                # Only explicit Graph rejection with a 4xx response proves no write.
                if method == "POST" and 400 <= status < 500:
                    self.boundary.proven_rejections += 1
                raise ProviderFailure(
                    failure, uncertain=method == "POST" and not (400 <= status < 500)
                )
            paging = result.get("paging")
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            cursor = cursors.get("after") if isinstance(cursors, dict) else None
            cleaned = _redact(result, (self.token, self.app_secret))
            cleaned.pop("_next_cursor", None)
            if (
                isinstance(cursor, str)
                and len(cursor) <= 2048
                and not any(ord(char) < 32 or ord(char) == 127 for char in cursor)
                and not any(secret and secret in cursor for secret in (self.token, self.app_secret))
            ):
                cleaned["_next_cursor"] = cursor
            return cleaned
        except ProviderFailure as error:
            if method == "POST" and (error.uncertain or error.code == "response_too_large"):
                raise ProviderFailure("ambiguous_external_effect", uncertain=True) from error
            raise
        except (TimeoutError, URLError, OSError, ValueError) as error:
            if method == "POST":
                raise ProviderFailure("ambiguous_external_effect", uncertain=True) from error
            code = (
                "timeout"
                if isinstance(error, TimeoutError)
                or isinstance(getattr(error, "reason", None), TimeoutError)
                else "provider_unavailable"
            )
            raise ProviderFailure(code) from error
