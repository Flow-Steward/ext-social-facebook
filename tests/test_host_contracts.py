"""Opt-in fs-audit interoperability with actual host routing/effect consumers.

Only this test module may import Core, and only with /app/core present. Persistence,
workflow lookup and enqueue boundaries are deterministic fakes: this does not prove
PostgreSQL atomicity, durable storage, signature verification or live job execution.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import (
    OTHER_PAGE,
    PAGE,
    POST,
    ROOT,
    TOKEN,
    action,
    comment_change,
    delivery,
    ingress,
    invoke,
)

pytestmark = pytest.mark.skipif(
    not Path("/app/core").is_dir(),
    reason="Host contract tests run only in managed fs-audit (/app/core)",
)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions import external_effect_replay, external_effects
    from core.application.management.command_models import ProviderEventIngestInput
    from core.application.management.command_services_event import EventCommandService
    from core.application.management.errors import ServiceForbiddenError
    from core.application.management.scope import ScopeContext
    from core.application.registry.workflow_contract_service import WorkflowInterfaceService
    from core.application.workflows import provider_event_trigger_dispatch as dispatch

    # Keep actual workflow input validation; replace its persisted-interface lookup.
    monkeypatch.setattr(
        WorkflowInterfaceService,
        "interface_for_workflow_id",
        lambda self, _workflow: self.interface_from_definition({"settings": {}, "steps": []}),
    )
    return SimpleNamespace(
        dispatch=dispatch,
        service=EventCommandService,
        command=ProviderEventIngestInput,
        scope=ScopeContext,
        forbidden=ServiceForbiddenError,
        effects=external_effects,
        replay=external_effect_replay,
    )


def trigger(trigger_id, page, *, project="project-a"):
    return {
        "trigger_id": trigger_id,
        "workflow_id": "workflow-" + trigger_id,
        "project_id": project,
        "provider_key": "flowsteward.facebook",
        "trigger_type_id": "facebook_comment_created",
        "display_name": "New comment",
        "event_name": "facebook.comment.created",
        "trigger_config_jsonb": {"page_id": page},
    }


class FakePersistence:
    def __init__(self):
        self.recorded = []
        self.claims = {}
        self.enqueued = []

    def get_project(self, project_id):
        return {"id": project_id, "account_id": "account-a"}

    def record_domain_event(self, **values):
        self.recorded.append(values)
        return {"event_id": values["event_id"]}

    def claim(self, _store, *, delivery_key, request_id, **kwargs):
        if delivery_key in self.claims:
            return {"claimed": False, "job_id": self.claims[delivery_key]}
        self.claims[delivery_key] = "job-" + str(len(self.claims) + 1)
        return {"claimed": True}

    def enqueue(self, **values):
        self.enqueued.append(values)
        return "job-" + str(len(self.enqueued))


@contextmanager
def admitted(_store, _row):
    # Fake the physical persistence fence, not the routing/filtering algorithm.
    yield True


def ingest_harness(host, monkeypatch):
    store = FakePersistence()
    rows = [
        trigger("page-a", PAGE),
        trigger("page-b", OTHER_PAGE),
        trigger("foreign-project", PAGE, project="project-b"),
        trigger("wrong-filter", "99999"),
    ]

    def listed(_store, *, provider_key):
        assert provider_key == "flowsteward.facebook"
        return rows

    monkeypatch.setattr(host.dispatch, "list_enabled_provider_triggers_for_provider", listed)
    monkeypatch.setattr(host.dispatch, "provider_trigger_admission", admitted)
    monkeypatch.setattr(
        host.dispatch,
        "resolve_project_run_config_for_workflow_project",
        lambda _store, _workflow, project: (project, {"project_name": "Fixture"}),
    )
    monkeypatch.setattr(host.dispatch, "_claim_provider_webhook_delivery", store.claim)
    monkeypatch.setattr(
        host.dispatch, "_mark_provider_webhook_delivery_accepted", lambda *a, **kw: 1
    )
    monkeypatch.setattr(host.dispatch, "_mark_provider_webhook_delivery_failed", lambda *a, **kw: 1)
    monkeypatch.setattr(host.dispatch, "enqueue_workflow_start", store.enqueue)
    service = host.service(
        get_logger=lambda: store,
        resolve_domain_event_by_ref=lambda *a, **kw: None,
        to_json_obj=lambda value: value if isinstance(value, dict) else {},
        serialize_domain_event_datetime=lambda value: str(value),
        enqueue_task=lambda **kw: None,
    )
    scope = host.scope(
        account_id="account-a", project_id="project-a", allowed_project_ids={"project-a"}
    )

    def consume(response, *, project="project-a"):
        assert response["ok"] and response["events"]
        return service.ingest_provider_events(
            host.command(
                provider_key="flowsteward.facebook",
                project_id=project,
                events=response["events"],
                result=response["result"],
            ),
            scope=scope,
        )

    return store, consume


def test_actual_normalized_events_replay_once_and_route_distinct_comment_and_page(
    host, monkeypatch
):
    store, consume = ingest_harness(host, monkeypatch)
    original = invoke(ingress(webhook_page_ids=[PAGE, OTHER_PAGE]))
    retried = ingress(webhook_page_ids=[PAGE, OTHER_PAGE])
    entry = retried["action"]["input"]["request"]["json"]["entry"][0]
    entry["time"] += 200
    entry["changes"][0]["value"].update(created_time="1794690002", message="Redelivered")
    again = invoke(retried)
    assert original["events"][0]["source_id"] == again["events"][0]["source_id"]
    consume(original)
    consume(again)
    assert len(store.enqueued) == len(store.claims) == 1

    changed_comment = invoke(
        ingress(
            delivery([comment_change(comment_id="30003_9")]), webhook_page_ids=[PAGE, OTHER_PAGE]
        )
    )
    post = OTHER_PAGE + "_3"
    changed_page = invoke(
        ingress(
            delivery([comment_change(post_id=post, parent_id=post)], page=OTHER_PAGE),
            webhook_page_ids=[PAGE, OTHER_PAGE],
        )
    )
    consume(changed_comment)
    consume(changed_page)
    assert len(store.enqueued) == len(store.claims) == 3
    assert [row["selected_workflow_id"] for row in store.enqueued] == [
        "workflow-page-a",
        "workflow-page-a",
        "workflow-page-b",
    ]
    emitted = [original["events"][0], changed_comment["events"][0], changed_page["events"][0]]
    assert {row["source_event_id"] for row in store.enqueued} == {
        row["source_id"] for row in emitted
    }
    assert all(row["project_id"] == "project-a" for row in store.enqueued)
    assert all(row["inputs"]["provider_key"] == "flowsteward.facebook" for row in store.enqueued)
    assert all(row["inputs"]["connection_id"] == "connection-anchor" for row in store.enqueued)
    assert all(row["account_id"] == "account-a" for row in store.recorded)
    assert len(store.recorded) == 4  # host records deliveries; workflow claims deduplicate replay.


def test_actual_host_ingestion_rejects_foreign_project_before_record_or_claim(host, monkeypatch):
    store, consume = ingest_harness(host, monkeypatch)
    with pytest.raises(host.forbidden):
        consume(invoke(ingress()), project="project-b")
    assert store.recorded == store.enqueued == []
    assert store.claims == {}


@pytest.mark.parametrize("outcome", ["ambiguous", "succeeded", "definitive_rejection"])
def test_actual_host_effect_confirmation_consumes_provider_metadata(
    host, graph, monkeypatch, outcome
):
    if outcome == "ambiguous":
        graph.fail(TimeoutError("unknown outcome"))
        expected = "timeout_unknown"
    elif outcome == "succeeded":
        graph.reply(200, {"id": POST})
        expected = "succeeded"
    else:
        graph.reply(403, {"error": {"code": 200}})
        expected = "failed"
    response = invoke(action("publish_post", {"text": "Once"}))
    writes = []
    effect = {
        "effect_id": "fixture-effect",
        "account_id": "account-a",
        "extension_id": "flowsteward.facebook",
        "extension_install_id": "fixture-install",
        "operation_id": "publish_post",
        "project_id": "project-a",
        "connection_id": "connection-page-a",
    }

    def persist(_store, **values):
        writes.append(copy.deepcopy(values))
        return {**effect, "status": values["status"]}

    monkeypatch.setattr(
        host.effects.external_effect_store, "get_external_effect", lambda *a, **kw: effect
    )
    monkeypatch.setattr(
        host.effects.external_effect_store, "update_external_effect_status", persist
    )
    monkeypatch.setattr(
        host.effects.external_effect_store, "confirm_external_effect_with_replay", persist
    )
    # Encryption belongs to the fake persistence boundary; real replay projection remains active.
    monkeypatch.setattr(
        host.replay,
        "encrypt_projected_replay",
        lambda *a, **kw: {
            "ciphertext": "fixture-only",
            "binding_hash": "fixture-binding",
            "plaintext_bytes": 100,
        },
    )
    operations = yaml.safe_load((ROOT / "contracts/operation_manifest.yaml").read_text())[
        "operations"
    ]
    confirmed = host.effects.confirm_external_effect_from_invocation(
        object(),
        effect_id="fixture-effect",
        claim_token="fixture-lease",
        account_id="account-a",
        operation_id="publish_post",
        extension={
            "extension_key": "flowsteward.facebook",
            "extension_install_id": "fixture-install",
            "operation_manifest": operations,
        },
        invocation={"status": "succeeded" if response["ok"] else "failed", "response": response},
        protected_values=(TOKEN,),
        expected_project_id="project-a",
        expected_connection_id="connection-page-a",
    )
    assert confirmed["status"] == writes[0]["status"] == expected
    assert len(writes) == len(graph.requests) == 1
    assert writes[0]["claim_token"] == "fixture-lease"
    if outcome == "succeeded":
        assert writes[0]["external_object_id"] == POST
        assert confirmed["__confirmed_replay_invocation"]["response"]["result"]["post_id"] == POST


def test_actual_host_compiler_accepts_package_locale_bindings(host):
    from core.infrastructure.extension_runtime.extension_runtime_compile import (
        compile_extension_bundle,
    )

    compiled = compile_extension_bundle(ROOT, logical_bundle_dir_name="flowsteward_facebook")
    assert compiled["lifecycle"]["compile"] == "ok", compiled.get("compile_errors")
    assert compiled["compiled_manifest"]["locale_bundles"].get("en")
    issues = [
        issue
        for field in (
            "compile_errors",
            "compile_warnings",
            "validation_errors",
            "validation_warnings",
        )
        for issue in compiled.get(field, [])
    ]
    assert not [
        issue for issue in issues if issue.get("code") in {"locale_orphan_key", "locale_dead_key"}
    ]
