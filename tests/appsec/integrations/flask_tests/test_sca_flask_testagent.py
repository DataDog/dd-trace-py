"""End-to-end tests for SCA runtime reachability via Flask + test agent.

Verifies that when DD_APPSEC_SCA_ENABLED=true:
1. Dependencies are reported with metadata: [] at startup.
2. When a vulnerable function is called, reachability metadata is attached
   and re-reported in subsequent app-dependencies-loaded events.

These tests require requests==2.31.0 (vulnerable to CVE-2024-35195) and
use the /sca-test-requests endpoint in tests/appsec/app.py which calls
requests.Session.send (the instrumented target).
"""

import base64
import json
import time

import pytest

from tests.appsec.appsec_utils import flask_server
from tests.appsec.integrations.utils_testagent import _get_agent_client


def _get_telemetry_requests(token):
    """Fetch all requests from the test agent for the given session token."""
    client = _get_agent_client()
    client.request(
        "GET",
        "/test/session/requests?test_session_token=%s" % token,
        headers={"X-Datadog-Test-Session-Token": token},
    )
    resp = client.getresponse()
    return json.loads(resp.read())


def _get_dependency_events(token):
    """Extract app-dependencies-loaded events from the test agent session."""
    requests = _get_telemetry_requests(token)
    events = []
    for req in requests:
        if "apmtelemetry" not in req.get("url", ""):
            continue
        try:
            body = json.loads(base64.b64decode(req["body"]))
        except Exception:
            continue
        if body.get("request_type") == "message-batch":
            for sub in body.get("payload", []):
                if sub.get("request_type") == "app-dependencies-loaded":
                    events.append(sub)
        elif body.get("request_type") == "app-dependencies-loaded":
            events.append({"payload": body["payload"], "request_type": body["request_type"]})
    return events


def _collect_all_deps(events):
    """Flatten all dependencies from all events into a single list."""
    all_deps = []
    for event in events:
        all_deps.extend(event.get("payload", {}).get("dependencies", []))
    return all_deps


def _find_dep_with_cve(events, dep_name, cve_id):
    """Find a dependency that has a specific CVE in its metadata."""
    for dep in _collect_all_deps(events):
        if dep.get("name") != dep_name:
            continue
        for meta_entry in dep.get("metadata", []):
            if meta_entry.get("type") != "reachability":
                continue
            try:
                value = json.loads(meta_entry["value"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if value.get("id") == cve_id:
                return dep, value
    return None, None


_SCA_ENV = {
    "DD_APPSEC_SCA_ENABLED": "true",
    "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
    "DD_TELEMETRY_HEARTBEAT_INTERVAL": "2",
}


class TestSCAFlaskTelemetry:
    """SCA telemetry e2e tests using Flask + test agent."""

    def test_sca_enabled_dependencies_have_metadata_key(self, iast_test_token):
        """When DD_APPSEC_SCA_ENABLED=true, dependency events include metadata key."""
        with flask_server(
            appsec_enabled="false",
            iast_enabled="false",
            token=iast_test_token,
            port=8050,
            env=_SCA_ENV,
        ) as context:
            _, flask_client, pid = context
            response = flask_client.get("/", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200
            time.sleep(3)

        events = _get_dependency_events(iast_test_token)
        assert len(events) > 0, "No app-dependencies-loaded events found"

        all_deps = _collect_all_deps(events)
        deps_with_metadata_key = [d for d in all_deps if "metadata" in d]
        assert len(deps_with_metadata_key) > 0, (
            f"Expected dependencies with metadata key when SCA enabled. "
            f"Got {len(all_deps)} total deps, none with metadata key. "
            f"Sample: {all_deps[:3]}"
        )

    def test_sca_disabled_dependencies_have_no_metadata_key(self, iast_test_token):
        """When DD_APPSEC_SCA_ENABLED is not set, no metadata key on dependencies."""
        with flask_server(
            appsec_enabled="false",
            iast_enabled="false",
            token=iast_test_token,
            port=8051,
            env={
                "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
                "DD_TELEMETRY_HEARTBEAT_INTERVAL": "2",
            },
        ) as context:
            _, flask_client, pid = context
            response = flask_client.get("/", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200
            time.sleep(3)

        events = _get_dependency_events(iast_test_token)
        all_deps = _collect_all_deps(events)
        deps_with_metadata_key = [d for d in all_deps if "metadata" in d]
        assert len(deps_with_metadata_key) == 0, (
            f"Expected no dependencies with metadata key when SCA disabled. "
            f"Got {len(deps_with_metadata_key)} deps with metadata. "
            f"Sample: {deps_with_metadata_key[:3]}"
        )

    def test_sca_reports_cve_metadata_after_vulnerable_call(self, iast_test_token):
        """When a vulnerable function is called, its CVE metadata is reported.

        This test:
        1. Starts Flask with SCA enabled and requests==2.31.0 (vulnerable)
        2. Hits /sca-test-requests which calls requests.Session.send
        3. Waits for telemetry to flush
        4. Verifies CVE-2024-35195 appears in the requests dependency metadata
        """
        with flask_server(
            appsec_enabled="false",
            iast_enabled="false",
            token=iast_test_token,
            port=8052,
            env=_SCA_ENV,
        ) as context:
            _, flask_client, pid = context

            # First request ensures server is warm and initial deps are reported
            response = flask_client.get("/", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200

            # Trigger the vulnerable code path
            response = flask_client.get(
                "/sca-test-requests", headers={"X-Datadog-Test-Session-Token": iast_test_token}
            )
            assert response.status_code == 200

            # Wait for telemetry to flush with the metadata
            time.sleep(4)

        events = _get_dependency_events(iast_test_token)
        assert len(events) > 0, "No app-dependencies-loaded events found"

        # Look for CVE-2024-35195 in the requests dependency metadata
        dep, cve_value = _find_dep_with_cve(events, "requests", "CVE-2024-35195")

        assert dep is not None, (
            "CVE-2024-35195 not found in requests dependency metadata. "
            f"Events: {json.dumps(events, indent=2)[:2000]}"
        )
        assert dep["name"] == "requests"
        assert cve_value["id"] == "CVE-2024-35195"
        assert cve_value["reached"] is True
        # AIDEV-NOTE: path/method/line now report the *caller* (user code that
        # invoked the vulnerable function), not the target function itself.
        # The caller is sca_test_requests() in tests/appsec/app.py.
        assert "app.py" in cve_value["path"], f"Expected caller path containing 'app.py', got: {cve_value['path']}"
        assert "sca_test_requests" in cve_value.get("method", ""), (
            f"Expected caller method 'sca_test_requests', got: {cve_value.get('method', '')}"
        )
        assert cve_value.get("line", 0) > 0, "Expected a non-zero caller line number"
