"""Selected-connection Page operations with ownership checks before reads/replies."""

from __future__ import annotations

import re
from typing import Any

from flowsteward_extension_sdk import RuntimeConnection, assert_safe_remote_https_url

from handlers.responses import ProviderFailure, success
from handlers.transport import GraphClient

WRITES = {"publish_post", "publish_photo", "reply_to_comment"}
OPERATIONS = WRITES | {
    "list_posts",
    "get_post",
    "list_comments",
    "list_replies",
    "verify_connection",
}
OBJECT_ID = re.compile(r"[0-9]{1,64}(?:_[0-9]{1,64})?\Z")
PAGE_ID = re.compile(r"[0-9]{1,64}\Z")
VERSION = re.compile(r"v[0-9]{1,3}\.[0-9]{1,3}\Z")


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
        raise ProviderFailure("invalid_payload")
    return value


def configured(connection: RuntimeConnection | None) -> tuple[str, GraphClient]:
    if connection is None or connection.connection_type != "facebook_page":
        raise ProviderFailure("not_configured")
    config, secrets = connection.config, connection.secrets
    page, app, version = (config.get(key) for key in ("page_id", "app_id", "graph_api_version"))
    token = secrets.get("access_token")
    if not (
        isinstance(page, str)
        and PAGE_ID.fullmatch(page)
        and isinstance(app, str)
        and PAGE_ID.fullmatch(app)
        and isinstance(version, str)
        and VERSION.fullmatch(version)
        and isinstance(token, str)
        and 0 < len(token.strip()) <= 4096
        and not any(ord(char) < 32 or ord(char) == 127 for char in token)
    ):
        raise ProviderFailure("not_configured")
    secret = secrets.get("app_secret")
    return page, GraphClient(version, token, secret if isinstance(secret, str) else "")


def _text(values: dict[str, Any], name: str, limit: int = 63206) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ProviderFailure("invalid_payload")
    return value


def _url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise ProviderFailure("invalid_payload")
    try:
        return assert_safe_remote_https_url(value)
    except ValueError as error:
        raise ProviderFailure("invalid_payload") from error


def _pagination(values: dict[str, Any]) -> dict[str, Any]:
    limit = values.get("limit", 25)
    cursor = values.get("cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ProviderFailure("invalid_payload")
    result: dict[str, Any] = {"limit": limit}
    if cursor is not None:
        if (
            not isinstance(cursor, str)
            or len(cursor) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in cursor)
        ):
            raise ProviderFailure("invalid_payload")
        if cursor:
            result["after"] = cursor
    return result


def _post_id(value: Any, page: str) -> str:
    post = identifier(value)
    if not post.startswith(page + "_"):
        raise ProviderFailure("foreign_object")
    return post


def _comment_owner(client: GraphClient, comment: str, page: str) -> None:
    result = client.request("GET", comment, {"fields": "id,object,parent"})
    object_value = result.get("object")
    owner = object_value.get("id") if isinstance(object_value, dict) else None
    if (
        result.get("id") != comment
        or not isinstance(owner, str)
        or not owner.startswith(page + "_")
        or not OBJECT_ID.fullmatch(owner)
    ):
        raise ProviderFailure("foreign_object")


def _published(result: dict[str, Any], key: str) -> dict[str, Any]:
    value = result.get("id")
    if not isinstance(value, str) or not OBJECT_ID.fullmatch(value):
        raise ProviderFailure("ambiguous_external_effect", uncertain=True)
    output = {key: value}
    if key == "photo_id" and isinstance(result.get("post_id"), str):
        output["post_id"] = result["post_id"]
    return success(output, object_id=value)


def execute(
    operation: str, values: dict[str, Any], connection: RuntimeConnection | None
) -> dict[str, Any]:
    page, client = configured(connection)
    if operation == "publish_post":
        fields = {"message": _text(values, "text")}
        if values.get("link_url") is not None:
            fields["link"] = _url(values["link_url"])
        return _published(client.request("POST", page + "/feed", fields), "post_id")
    if operation == "publish_photo":
        fields = {"url": _url(values.get("image_url")), "caption": _text(values, "text")}
        return _published(client.request("POST", page + "/photos", fields), "photo_id")
    if operation == "verify_connection":
        result = client.request("GET", page, {"fields": "id,name"})
        if result.get("id") != page:
            raise ProviderFailure("foreign_object")
        return success({"verified": True, "page_id": page})
    if operation == "get_post":
        post = _post_id(values.get("post_id"), page)
        result = client.request(
            "GET", post, {"fields": "id,message,created_time,permalink_url,from"}
        )
        if result.get("id") != post:
            raise ProviderFailure("foreign_object")
        return success({"post": result})
    if operation == "reply_to_comment":
        comment = identifier(values.get("comment_id"))
        message = _text(values, "text", 8000)
        _comment_owner(client, comment, page)
        return _published(
            client.request("POST", comment + "/comments", {"message": message}), "comment_id"
        )
    params = _pagination(values)
    if operation == "list_posts":
        path = page + "/posts"
        params["fields"] = "id,message,created_time,permalink_url,from"
    elif operation == "list_comments":
        path = _post_id(values.get("post_id"), page) + "/comments"
        params["fields"] = "id,message,created_time,from,parent"
    elif operation == "list_replies":
        comment = identifier(values.get("comment_id"))
        _comment_owner(client, comment, page)
        path = comment + "/comments"
        params["fields"] = "id,message,created_time,from,parent"
    else:
        raise ProviderFailure("invalid_payload")
    result = client.request("GET", path, params)
    items = result.get("data")
    if not isinstance(items, list):
        raise ProviderFailure("provider_unavailable")
    # The client strips paging URLs and secrets; retain only the safe opaque cursor
    # via its dedicated request-local pagination field.
    return success({"items": items[: params["limit"]], "next_cursor": result.get("_next_cursor")})
