"""Normalize authenticated root comments; durable replay claims belong to the host."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from flowsteward_extension_sdk import RuntimeContext

from handlers.operations import OBJECT_ID, PAGE_ID
from handlers.responses import ProviderFailure, success

PROVIDER = "flowsteward.facebook"


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize(values: dict[str, Any], context: RuntimeContext) -> dict[str, Any]:
    callback, request = _object(values.get("callback")), _object(values.get("request"))
    provider = _object(request.get("provider"))
    project, connection = callback.get("project_id"), callback.get("connection_id")
    if (
        callback.get("authenticated") is not True
        or callback.get("extension_id") != PROVIDER
        or callback.get("callback_id") != "comments"
        or not isinstance(project, str)
        or not project
        or not isinstance(connection, str)
        or not connection
        or context.project_id != project
        or request.get("method") != "POST"
        or provider.get("provider_key") != PROVIDER
        or provider.get("project_id") != project
        or provider.get("connection_id") != connection
    ):
        raise ProviderFailure("authentication_failed")
    config = _object(provider.get("provider_config"))
    anchor_page = config.get("page_id")
    allowed = config.get("webhook_page_ids", [anchor_page])
    if (
        not isinstance(anchor_page, str)
        or not PAGE_ID.fullmatch(anchor_page)
        or not isinstance(allowed, list)
        or len(allowed) > 100
        or not all(isinstance(page, str) and PAGE_ID.fullmatch(page) for page in allowed)
    ):
        raise ProviderFailure("not_configured")
    own_pages = {*allowed, anchor_page}
    body = _object(request.get("json"))
    emitted = []
    entries = body.get("entry")
    if body.get("object") != "page" or not isinstance(entries, list):
        return success({"ingress_type": "facebook.comment.created", "accepted": 0})
    for entry in entries:
        entry = _object(entry)
        page, changes = entry.get("id"), entry.get("changes")
        if page not in allowed or not isinstance(changes, list):
            continue
        for change in changes:
            change = _object(change)
            value = _object(change.get("value"))
            author = _object(value.get("from")).get("id")
            post, comment = value.get("post_id"), value.get("comment_id")
            created = value.get("created_time")
            if (
                change.get("field") != "feed"
                or value.get("item") != "comment"
                or value.get("verb") != "add"
                or not isinstance(author, str)
                or not PAGE_ID.fullmatch(author)
                or author in own_pages
                or not isinstance(post, str)
                or not OBJECT_ID.fullmatch(post)
                or not post.startswith(page + "_")
                or value.get("parent_id") != post
                or not isinstance(comment, str)
                or not OBJECT_ID.fullmatch(comment)
                or not isinstance(value.get("message"), str)
                or len(value["message"]) > 63206
                or isinstance(created, bool)
                or not isinstance(created, (int, str))
                or not str(created).isdigit()
            ):
                continue
            identity = [
                PROVIDER,
                context.account_id,
                project,
                connection,
                page,
                post,
                comment,
                "created",
            ]
            dedupe = (
                "facebook:comment:created:"
                + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            )
            event_payload = {
                "provider": PROVIDER,
                "project_id": project,
                "connection_id": connection,
                "page_id": page,
                "post_id": post,
                "comment_id": comment,
                "author_id": author,
                "text": value["message"],
                "created_time": created,
                "dedupe_key": dedupe,
            }
            emitted.append(
                {
                    "event_type": "facebook.comment.created",
                    "project_id": project,
                    "source_id": dedupe,
                    "correlation_id": dedupe,
                    "payload": event_payload,
                }
            )
    return success(
        {"ingress_type": "facebook.comment.created", "accepted": len(emitted)}, events=emitted
    )
