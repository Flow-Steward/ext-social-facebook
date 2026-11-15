"""Real stdin/stdout and credential-free readiness checks."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import ROOT, action, envelope, invoke


def test_no_account_reports_not_configured_in_readiness():
    response = invoke(
        envelope(
            {
                "mode": "query",
                "query": {"query_id": "provider_health", "context": {"project_id": "project-a"}},
            }
        )
    )
    assert response["result"]["status"] == "not_configured"
    assert response["result"]["configured"] is False


def test_install_health_is_local_and_needs_no_real_account():
    process = subprocess.run(
        [sys.executable, "health.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert process.returncode == 0
    assert json.loads(process.stdout)["status"] == "healthy"


def test_subprocess_domain_failure_is_canonical_and_exit_two():
    request = action("publish_post", {"text": "Hello"})
    request["action"]["target"] = {}
    process = subprocess.run(
        [sys.executable, "main.py"],
        cwd=ROOT,
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    response = json.loads(process.stdout)
    assert process.returncode == 2
    assert response["ok"] is False
    assert response["error_code"] == "not_configured"
    assert response["errors"][0] == {"code": response["error_code"], "message": response["error"]}
    assert process.stderr == ""


def test_readiness_allows_publishing_without_webhook_only_app_secret():
    request = action("publish_post")
    connection = request["action"]["target"]["connection"]
    connection["secrets"].pop("app_secret")
    request.update(
        mode="query", query={"query_id": "provider_health"}, target={"connection": connection}
    )
    request.pop("action")
    response = invoke(request)
    assert response["ok"] is True
    assert response["result"]["configured"] is True
    assert response["result"]["webhook_configured"] is False
