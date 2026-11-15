"""Behavioral provider contract. The scaffold must fail the first publish assertion."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import COMMENT, OTHER_PAGE, PAGE, POST, TOKEN, action, invoke

from handlers import operations


def test_publish_post_maps_message_and_link_to_selected_page(graph):
    graph.reply(200, {"id": POST})
    response = invoke(
        action("publish_post", {"text": "Hello Page", "link_url": "https://example.org/news"})
    )
    assert response["ok"] is True, response
    assert response["result"]["post_id"] == POST
    assert response["external_effect_status"] == "succeeded"
    assert response["external_object_id"] == POST
    assert graph.requests[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert graph.form_body() == {"message": "Hello Page", "link": "https://example.org/news"}
    assert graph.requests[0]["method"] == "POST"
    assert graph.requests[0]["url"] == f"https://graph.facebook.com/v27.0/{PAGE}/feed"
    assert graph.requests[0]["headers"]["Authorization"] == "Bearer " + TOKEN
    assert TOKEN not in graph.requests[0]["url"]
    assert 0 < graph.requests[0]["timeout"] <= 30
    assert 0 < graph.requests[0]["max_bytes"] <= 2 * 1024 * 1024


def test_publish_photo_maps_remote_image_and_caption(graph):
    graph.reply(200, {"id": "70007", "post_id": POST})
    response = invoke(
        action("publish_photo", {"image_url": "https://example.org/photo.png", "text": "A photo"})
    )
    assert response["ok"] is True, response
    assert response["result"]["photo_id"] == "70007"
    assert graph.requests[0]["url"].endswith(f"/{PAGE}/photos")
    assert graph.requests[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert graph.form_body() == {"url": "https://example.org/photo.png", "caption": "A photo"}


def test_outgoing_uses_selected_page_and_token_without_intake_anchor_remapping(graph):
    request = action("publish_post", {"text": "Other Page"}, page_id=OTHER_PAGE)
    request["action"]["target"]["connection"]["connection_id"] = "connection-page-b"
    request["action"]["target"]["connection"]["secrets"]["access_token"] = "fixture-token-b"
    graph.reply(200, {"id": OTHER_PAGE + "_1"})
    response = invoke(request)
    assert response["ok"] is True, response
    assert graph.requests[0]["url"].endswith(f"/{OTHER_PAGE}/feed")
    assert graph.requests[0]["headers"]["Authorization"] == "Bearer fixture-token-b"


def test_comment_reply_checks_parent_page_before_posting(graph):
    graph.reply(200, {"id": COMMENT, "object": {"id": POST}, "parent": {"id": POST}})
    graph.reply(200, {"id": "40004_80008"})
    response = invoke(action("reply_to_comment", {"comment_id": COMMENT, "text": "Thanks"}))
    assert response["ok"] is True, response
    assert response["result"]["comment_id"] == "40004_80008"
    assert [request["method"] for request in graph.requests] == ["GET", "POST"]
    assert graph.requests[-1]["url"].endswith(f"/{COMMENT}/comments")
    assert graph.requests[-1]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert graph.form_body() == {"message": "Thanks"}


@pytest.mark.parametrize("operation", ["reply_to_comment", "list_replies"])
def test_foreign_comment_is_rejected_before_reading_replies_or_writing(graph, operation):
    graph.reply(
        200,
        {"id": COMMENT, "object": {"id": OTHER_PAGE + "_3"}, "parent": {"id": OTHER_PAGE + "_3"}},
    )
    response = invoke(action(operation, {"comment_id": COMMENT, "text": "No"}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert len(graph.requests) == 1
    assert graph.requests[0]["method"] == "GET"


@pytest.mark.parametrize("operation", ["get_post", "list_comments"])
def test_foreign_post_is_rejected_without_provider_request(graph, operation):
    response = invoke(action(operation, {"post_id": OTHER_PAGE + "_30003"}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert graph.requests == []


@pytest.mark.parametrize("missing", ["page_id", "app_id", "graph_api_version", "access_token"])
def test_missing_configuration_never_calls_provider(graph, missing):
    request = action("publish_post", {"text": "hello"})
    connection = request["action"]["target"]["connection"]
    connection["secrets" if missing == "access_token" else "config"].pop(missing)
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "not_configured"
    assert graph.requests == []


def test_missing_selected_connection_does_not_discover_another_connection(graph):
    request = action("publish_post", {"text": "hello"})
    request["action"]["target"] = {}
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "not_configured"
    assert graph.requests == []


@pytest.mark.parametrize(
    "operation, values",
    [
        ("publish_post", {"text": ""}),
        ("publish_photo", {"image_url": "http://example.org/a.png", "text": "x"}),
        ("publish_photo", {"image_url": "https://name:password@example.org/a.png", "text": "x"}),
        ("get_post", {"post_id": "../me"}),
        ("reply_to_comment", {"comment_id": "x?access_token=bad", "text": "x"}),
        ("list_posts", {"limit": 101}),
        ("list_posts", {"limit": 0}),
        ("list_posts", {"limit": True}),
    ],
)
def test_invalid_input_is_refused_without_provider_traffic(graph, operation, values):
    response = invoke(action(operation, values))
    assert response["ok"] is False
    assert response["error_code"] == "invalid_payload"
    assert graph.requests == []


def test_get_post_returns_selected_page_object(graph):
    graph.reply(200, {"id": POST, "message": "Hello", "from": {"id": PAGE}})
    response = invoke(action("get_post", {"post_id": POST}))
    assert response["ok"] is True, response
    assert response["result"]["post"]["id"] == POST
    assert graph.requests[0]["method"] == "GET"


@pytest.mark.parametrize(
    "operation, values, suffix",
    [
        ("list_posts", {}, f"/{PAGE}/posts"),
        ("list_comments", {"post_id": POST}, f"/{POST}/comments"),
        ("list_replies", {"comment_id": COMMENT}, f"/{COMMENT}/comments"),
    ],
)
def test_list_returns_bounded_items_and_opaque_cursor_without_following_next_url(
    graph, operation, values, suffix
):
    if operation == "list_replies":
        graph.reply(200, {"id": COMMENT, "object": {"id": POST}, "parent": {"id": POST}})
    graph.reply(
        200,
        {
            "data": [{"id": "1", "message": "Hi"}],
            "paging": {
                "cursors": {"after": "opaque+/="},
                "next": "https://evil.example/steal?access_token=" + TOKEN,
            },
        },
    )
    response = invoke(action(operation, {**values, "limit": 7, "cursor": "input+/="}))
    assert response["ok"] is True, response
    assert response["result"]["items"] == [{"id": "1", "message": "Hi"}]
    assert response["result"]["next_cursor"] == "opaque+/="
    request = graph.requests[-1]
    split = urlsplit(request["url"])
    assert split.netloc == "graph.facebook.com"
    assert split.path.endswith(suffix)
    query = parse_qs(split.query)
    assert query["limit"] == ["7"]
    assert query["after"] == ["input+/="]
    assert TOKEN not in json.dumps(response)
    assert "evil.example" not in json.dumps(response)
    assert len(graph.requests) == (2 if operation == "list_replies" else 1)


@pytest.mark.parametrize(
    "status, provider_code, expected",
    [
        (401, 190, "authentication_failed"),
        (403, 200, "permission_denied"),
        (429, 4, "rate_limited"),
        (503, 2, "provider_unavailable"),
    ],
)
def test_provider_error_is_classified_and_secret_text_is_redacted(
    graph, capsys, status, provider_code, expected
):
    graph.reply(status, {"error": {"code": provider_code, "message": "unsafe " + TOKEN}})
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert response["error_code"] == expected
    assert response["errors"][0]["code"] == expected
    assert response["errors"][0]["message"] == response["error"]
    captured = capsys.readouterr()
    assert TOKEN not in json.dumps(response) + captured.out + captured.err


def test_redirect_is_refused_without_following_foreign_location(graph):
    graph.reply(302, b"", {"Location": "https://evil.example/"})
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert len(graph.requests) == 1


def test_write_timeout_is_ambiguous_and_not_automatically_retried(graph):
    graph.fail(TimeoutError("request may have reached provider " + TOKEN))
    response = invoke(action("publish_post", {"text": "Once"}))
    assert response["ok"] is False
    assert response["error_code"] == "ambiguous_external_effect"
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 1
    assert TOKEN not in json.dumps(response)


def test_read_timeout_is_domain_failure(graph):
    graph.fail(TimeoutError("timeout"))
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert response["error_code"] == "timeout"


def test_oversized_provider_response_is_refused(graph):
    graph.reply(200, b"x" * (2 * 1024 * 1024 + 1))
    response = invoke(action("list_posts"))
    assert response["ok"] is False
    assert response["error_code"] == "response_too_large"


def test_verify_connection_only_reads_and_compares_provider_identity(graph):
    graph.reply(200, {"id": PAGE, "name": "Test Page"})
    response = invoke(action("verify_connection"))
    assert response["ok"] is True, response
    assert response["result"]["verified"] is True
    assert response["result"]["page_id"] == PAGE
    assert [request["method"] for request in graph.requests] == ["GET"]


def test_verify_connection_rejects_wrong_page_identity(graph):
    graph.reply(200, {"id": OTHER_PAGE})
    response = invoke(action("verify_connection"))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"


def test_post_publish_does_not_require_webhook_only_app_secret(graph):
    request = action("publish_post", {"text": "Publish without webhook credentials"})
    request["action"]["target"]["connection"]["secrets"].pop("app_secret")
    graph.reply(200, {"id": POST})
    response = invoke(request)
    assert response["ok"] is True, response
    assert len(graph.requests) == 1


@pytest.mark.parametrize("status, code", [(401, 190), (403, 200), (429, 4)])
def test_definitive_provider_write_rejection_reports_no_external_effect(graph, status, code):
    graph.reply(status, {"error": {"code": code, "message": "Rejected"}})
    response = invoke(action("publish_post", {"text": "Rejected post"}))
    assert response["ok"] is False
    assert response["external_effect_status"] == "failed"
    assert response["definitely_no_external_effect"] is True
    assert len(graph.requests) == 1


def test_missing_write_credentials_reports_definitive_preflight_failure(graph):
    request = action("publish_post", {"text": "No credentials"})
    request["action"]["target"]["connection"]["secrets"] = {}
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "not_configured"
    assert response["definitely_no_external_effect"] is True
    assert graph.requests == []


def test_comment_without_proven_page_ownership_cannot_be_replied_to(graph):
    graph.reply(200, {"id": COMMENT})
    response = invoke(action("reply_to_comment", {"comment_id": COMMENT, "text": "No proof"}))
    assert response["ok"] is False
    assert response["error_code"] == "foreign_object"
    assert response["definitely_no_external_effect"] is True
    assert [request["method"] for request in graph.requests] == ["GET"]


@pytest.mark.parametrize("body", [b"not-json", {"success": True}])
def test_write_without_success_identity_is_ambiguous(graph, body):
    graph.reply(200, body)
    response = invoke(action("publish_post", {"text": "Once"}))
    assert response["error_code"] == "ambiguous_external_effect"
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert len(graph.requests) == 1


def test_reply_ownership_read_timeout_proves_no_write_was_sent(graph):
    graph.fail(TimeoutError("read timeout"))
    response = invoke(action("reply_to_comment", {"comment_id": COMMENT, "text": "Once"}))
    assert response["error_code"] == "timeout"
    assert response["definitely_no_external_effect"] is True
    assert [request["method"] for request in graph.requests] == ["GET"]


@pytest.mark.parametrize(
    "status, provider_code",
    [
        pytest.param(200, [], id="ok-list"),
        pytest.param(200, {}, id="ok-map"),
        pytest.param(503, [], id="unavailable-list"),
        pytest.param(503, {}, id="unavailable-map"),
    ],
)
def test_malformed_error_code_after_write_has_unknown_effect(graph, status, provider_code):
    graph.reply(status, {"error": {"code": provider_code, "message": "unsafe " + TOKEN}})
    response = invoke(action("publish_post", {"text": "Send only once"}))
    assert response["ok"] is False
    assert response["error_code"] == "ambiguous_external_effect", response
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert [request["method"] for request in graph.requests] == ["POST"]
    assert TOKEN not in json.dumps(response)


@pytest.mark.parametrize(
    "operation, values",
    [
        pytest.param("publish_post", {"text": "Once"}, id="post"),
        pytest.param(
            "publish_photo",
            {"image_url": "https://example.org/photo.png", "text": "Once"},
            id="photo",
        ),
        pytest.param("reply_to_comment", {"comment_id": COMMENT, "text": "Once"}, id="reply"),
    ],
)
@pytest.mark.parametrize(
    "exception_type",
    [
        pytest.param(ValueError, id="value-error"),
        pytest.param(TypeError, id="type-error"),
        pytest.param(KeyError, id="key-error"),
    ],
)
def test_unexpected_post_send_result_error_cannot_prove_no_effect(
    graph,
    monkeypatch,
    operation,
    values,
    exception_type,
):
    if operation == "reply_to_comment":
        graph.reply(200, {"id": COMMENT, "object": {"id": POST}, "parent": {"id": POST}})
    graph.reply(200, {"id": POST})

    def broken_result_mapping(*args, **kwargs):
        # Fault injection occurs after the real transport has returned a POST
        # response, outside its exception handler. Exception type is not proof
        # that the provider has done nothing.
        assert graph.requests[-1]["method"] == "POST"
        raise exception_type("post-send parsing failure " + TOKEN)

    monkeypatch.setattr(operations, "_published", broken_result_mapping)
    response = invoke(action(operation, values))
    assert response["ok"] is False
    assert response["error_code"] == "ambiguous_external_effect", response
    assert response["external_effect_status"] == "timeout_unknown"
    assert response.get("definitely_no_external_effect") is not True
    assert sum(request["method"] == "POST" for request in graph.requests) == 1
    assert len(graph.requests) == (2 if operation == "reply_to_comment" else 1)
    assert TOKEN not in json.dumps(response)
