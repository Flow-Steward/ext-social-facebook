"""Authenticated Page feed normalization, project binding and replay identity."""

from __future__ import annotations

import copy

import pytest
from conftest import COMMENT, OTHER_PAGE, PAGE, POST, comment_change, delivery, ingress, invoke


def events(request):
    response = invoke(request)
    assert response["ok"] is True, response
    return response["events"]


def test_new_root_comment_emits_documented_scoped_event():
    emitted = events(ingress())
    assert len(emitted) == 1
    event = emitted[0]
    assert event["event_type"] == "facebook.comment.created"
    assert event["project_id"] == "project-a"
    payload = event["payload"]
    expected = {
        "provider": "flowsteward.facebook",
        "project_id": "project-a",
        "connection_id": "connection-anchor",
        "page_id": PAGE,
        "post_id": POST,
        "comment_id": COMMENT,
        "author_id": "60006",
        "text": "A useful question",
        "created_time": 1794690000,
    }
    assert {key: payload[key] for key in expected} == expected
    assert event["source_id"] == event["correlation_id"] == payload["dedupe_key"]
    assert event["source_id"]


@pytest.mark.parametrize(
    "change",
    [
        comment_change(verb="edited"),
        comment_change(verb="remove"),
        comment_change(verb="hide"),
        comment_change(item="post"),
        comment_change(**{"from": {"id": PAGE}}),
        comment_change(parent_id=COMMENT),
        comment_change(post_id=OTHER_PAGE + "_3"),
        comment_change(comment_id=""),
        comment_change(created_time=None),
    ],
)
def test_non_new_own_reply_foreign_and_incomplete_comments_are_ignored(change):
    assert events(ingress(delivery([change]))) == []


def test_another_allowed_page_author_is_own_account_and_is_ignored():
    change = comment_change(**{"from": {"id": OTHER_PAGE}})
    assert events(ingress(delivery([change]), webhook_page_ids=[PAGE, OTHER_PAGE])) == []


def test_foreign_page_entry_is_ignored_even_if_payload_claims_local_post():
    assert events(ingress(delivery(page=OTHER_PAGE))) == []


def test_multiple_entries_and_changes_all_normalize_without_cross_page_leakage():
    body = delivery([comment_change(), comment_change(comment_id="30003_40005")])
    body["entry"].append({"id": OTHER_PAGE, "changes": [comment_change(post_id=OTHER_PAGE + "_3")]})
    emitted = events(ingress(body))
    assert [event["payload"]["comment_id"] for event in emitted] == [COMMENT, "30003_40005"]
    assert len({event["source_id"] for event in emitted}) == 2


def test_explicit_same_app_page_allowlist_retains_authenticated_anchor_connection():
    body = delivery(
        [comment_change(post_id=OTHER_PAGE + "_3", parent_id=OTHER_PAGE + "_3")], page=OTHER_PAGE
    )
    emitted = events(ingress(body, webhook_page_ids=[PAGE, OTHER_PAGE]))
    assert len(emitted) == 1
    assert emitted[0]["payload"]["page_id"] == OTHER_PAGE
    assert emitted[0]["payload"]["connection_id"] == "connection-anchor"
    assert emitted[0]["project_id"] == "project-a"


def test_explicit_empty_allowlist_does_not_fall_back_to_configured_page():
    assert events(ingress(webhook_page_ids=[])) == []


@pytest.mark.parametrize(
    "field, value", [("authenticated", False), ("connection_id", ""), ("project_id", "")]
)
def test_unauthenticated_or_unscoped_callback_is_rejected(field, value):
    request = ingress()
    request["action"]["input"]["callback"][field] = value
    response = invoke(request)
    assert response["ok"] is False
    assert response["error_code"] == "authentication_failed"
    assert response["events"] == []


@pytest.mark.parametrize(
    "field, value",
    [
        ("connection_id", "foreign-connection"),
        ("project_id", "foreign-project"),
        ("provider_key", "foreign.extension"),
    ],
)
def test_provider_resource_must_match_authenticated_callback_scope(field, value):
    request = ingress()
    request["action"]["input"]["request"]["provider"][field] = value
    response = invoke(request)
    assert response["ok"] is False
    assert response["events"] == []


def test_body_supplied_project_and_connection_cannot_remap_intake():
    change = comment_change(project_id="victim-project", connection_id="victim-connection")
    body = delivery([change])
    body.update(project_id="victim-project", connection_id="victim-connection")
    emitted = events(ingress(body))
    assert emitted[0]["project_id"] == "project-a"
    assert emitted[0]["payload"]["connection_id"] == "connection-anchor"


@pytest.mark.parametrize("created_time", [1794690002, "1794690000"])
def test_repeat_delivery_keeps_identity_across_fresh_runtime_requests(created_time):
    first = events(ingress())[0]
    retried = ingress()
    # Delivery envelope time and message representation are not event identity.
    entry = retried["action"]["input"]["request"]["json"]["entry"][0]
    entry["time"] += 300
    entry["changes"][0]["value"].update(
        created_time=created_time, message="Reformatted delivery text"
    )
    again = events(copy.deepcopy(retried))[0]
    assert first["source_id"] == again["source_id"]
    assert first["correlation_id"] == again["correlation_id"]
    # Both deliveries reach the existing durable host ledger; process memory is not the deduper.
    assert first["payload"]["dedupe_key"] == again["payload"]["dedupe_key"]


def test_created_event_identity_is_namespaced_by_provider_connection_page_post_and_comment():
    original = events(ingress())[0]["source_id"]
    altered = []
    request = ingress()
    request["action"]["input"]["callback"]["connection_id"] = "anchor-b"
    request["action"]["input"]["request"]["provider"]["connection_id"] = "anchor-b"
    altered.append(request)
    altered.append(ingress(delivery([comment_change(post_id=PAGE + "_9", parent_id=PAGE + "_9")])))
    altered.append(ingress(delivery([comment_change(comment_id="30003_9")])))
    altered.append(
        ingress(
            delivery(
                [comment_change(post_id=OTHER_PAGE + "_3", parent_id=OTHER_PAGE + "_3")],
                page=OTHER_PAGE,
            ),
            webhook_page_ids=[PAGE, OTHER_PAGE],
        )
    )
    ids = [events(request)[0]["source_id"] for request in altered]
    assert original not in ids
    assert len(set(ids)) == len(ids)
    assert "facebook" in original


@pytest.mark.parametrize("body", [{"object": "user", "entry": []}, {"object": "page", "entry": []}])
def test_unrelated_or_empty_provider_batch_emits_nothing(body):
    assert events(ingress(body)) == []


def test_anchor_authored_comment_is_ignored_when_intake_allowlist_excludes_anchor():
    post = OTHER_PAGE + "_3"
    body = delivery(
        [comment_change(post_id=post, parent_id=post, **{"from": {"id": PAGE}})],
        page=OTHER_PAGE,
    )
    assert events(ingress(body, webhook_page_ids=[OTHER_PAGE])) == []


def test_foreign_person_on_allowed_page_retains_excluded_anchor_identity():
    post = OTHER_PAGE + "_3"
    body = delivery([comment_change(post_id=post, parent_id=post)], page=OTHER_PAGE)
    emitted = events(ingress(body, webhook_page_ids=[OTHER_PAGE]))
    assert len(emitted) == 1
    event = emitted[0]
    assert event["project_id"] == "project-a"
    assert event["payload"]["connection_id"] == "connection-anchor"
    assert event["payload"]["page_id"] == OTHER_PAGE
    assert event["payload"]["post_id"] == post
    assert event["payload"]["comment_id"] == COMMENT
    assert event["payload"]["author_id"] == "60006"
    assert event["source_id"] == event["correlation_id"] == event["payload"]["dedupe_key"]
