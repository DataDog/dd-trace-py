"""End-to-end tests for SCA runtime reachability via Flask + test agent.

Verifies that when DD_APPSEC_SCA_ENABLED=true:
1. Dependencies are reported with metadata: [] at startup.
2. When CVEs are loaded, they appear with reached: [] in metadata.
3. When a vulnerable function is called, reachability metadata is attached
   with the caller info in the reached array.

These tests require requests==2.31.0 (vulnerable to CVE-2024-35195) and
use the /sca-test-requests endpoint in tests/appsec/app.py which calls
requests.Session.send (the instrumented target).
"""

import base64
import json
import time

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


def _find_all_cve_metadata(events, dep_name, cve_id):
    """Collect ALL metadata entries for a specific CVE across all events.

    Returns a list of parsed value dicts for the given CVE.
    """
    results = []
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
                results.append(value)
    return results


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
        4. Verifies CVE-2024-35195 appears with reached array containing caller info
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
            response = flask_client.get("/sca-test-requests", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200

            # Wait for telemetry to flush with the metadata
            time.sleep(4)

        events = _get_dependency_events(iast_test_token)
        assert len(events) > 0, "No app-dependencies-loaded events found"

        # Look for CVE-2024-35195 in the requests dependency metadata
        dep, cve_value = _find_dep_with_cve(events, "requests", "CVE-2024-35195")

        assert dep is not None, (
            f"CVE-2024-35195 not found in requests dependency metadata. Events: {json.dumps(events, indent=2)[:2000]}"
        )
        assert dep["name"] == "requests"
        assert cve_value["id"] == "CVE-2024-35195"
        # AIDEV-NOTE: RFC v3 — reached is now an array of {path, method, line} objects.
        assert isinstance(cve_value["reached"], list)
        assert len(cve_value["reached"]) >= 1
        hit = cve_value["reached"][0]
        # AIDEV-NOTE: path/method/line report the *caller* (user code that
        # invoked the vulnerable function), not the target function itself.
        assert "app.py" in hit["path"], f"Expected caller path containing 'app.py', got: {hit['path']}"
        assert "sca_test_requests" in hit.get("symbol", ""), (
            f"Expected caller symbol 'sca_test_requests', got: {hit.get('symbol', '')}"
        )
        assert hit.get("line", 0) > 0, "Expected a non-zero caller line number"

    def test_sca_same_cve_first_hit_wins(self, iast_test_token):
        """Same CVE triggered from two different functions — first hit wins (max reached=1).

        /sca-test-requests and /sca-test-requests-alt both call
        requests.Session.send (CVE-2024-35195) but from different functions.
        Per RFC v3, only the first occurrence is reported in the reached array.
        """
        with flask_server(
            appsec_enabled="false",
            iast_enabled="false",
            token=iast_test_token,
            port=8053,
            env=_SCA_ENV,
        ) as context:
            _, flask_client, pid = context

            response = flask_client.get("/", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200

            # Hit two different endpoints that trigger the same CVE
            response = flask_client.get("/sca-test-requests", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200

            response = flask_client.get(
                "/sca-test-requests-alt", headers={"X-Datadog-Test-Session-Token": iast_test_token}
            )
            assert response.status_code == 200

            time.sleep(4)

        events = _get_dependency_events(iast_test_token)
        assert len(events) > 0, "No app-dependencies-loaded events found"

        all_cve_entries = _find_all_cve_metadata(events, "requests", "CVE-2024-35195")
        assert len(all_cve_entries) >= 1, (
            f"Expected at least 1 metadata entry for CVE-2024-35195, got {len(all_cve_entries)}: {all_cve_entries}"
        )

        # Per RFC v3, each CVE entry has max 1 reached entry (first hit wins)
        for entry in all_cve_entries:
            assert isinstance(entry["reached"], list)
            assert len(entry["reached"]) <= 1, (
                f"Expected at most 1 reached entry per CVE (first hit wins), "
                f"got {len(entry['reached'])}: {entry['reached']}"
            )

    def test_sca_deduplication_repeated_calls(self, iast_test_token):
        """Calling the same vulnerable function multiple times from the same call site
        produces only ONE reached entry per CVE.

        This test hits /sca-test-requests five times.  Each call triggers the
        SCA hook at the same call site (same file, function, line).  The
        reached array should contain at most 1 entry (first hit wins).
        """
        with flask_server(
            appsec_enabled="false",
            iast_enabled="false",
            token=iast_test_token,
            port=8054,
            env=_SCA_ENV,
        ) as context:
            _, flask_client, pid = context

            response = flask_client.get("/", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200

            # Hit the same endpoint multiple times
            for _ in range(5):
                response = flask_client.get(
                    "/sca-test-requests", headers={"X-Datadog-Test-Session-Token": iast_test_token}
                )
                assert response.status_code == 200

            time.sleep(4)

        events = _get_dependency_events(iast_test_token)
        assert len(events) > 0, "No app-dependencies-loaded events found"

        all_cve_entries = _find_all_cve_metadata(events, "requests", "CVE-2024-35195")
        assert len(all_cve_entries) >= 1, "Expected at least one CVE-2024-35195 metadata entry"

        # All entries should have at most 1 reached entry
        for entry in all_cve_entries:
            assert len(entry.get("reached", [])) <= 1, (
                f"Expected at most 1 reached entry per CVE, got {len(entry['reached'])}"
            )

    def test_sca_cve_registered_at_load_time(self, iast_test_token):
        """CVEs appear with reached=[] in telemetry without triggering any vulnerable endpoint.

        When SCA loads CVE data at startup, it registers all applicable CVEs
        on their dependencies with reached=[].  This test verifies that
        behavior without hitting any vulnerable code path.
        """
        with flask_server(
            appsec_enabled="false",
            iast_enabled="false",
            token=iast_test_token,
            port=8055,
            env=_SCA_ENV,
        ) as context:
            _, flask_client, pid = context

            # Only hit the root endpoint (no vulnerable code)
            response = flask_client.get("/", headers={"X-Datadog-Test-Session-Token": iast_test_token})
            assert response.status_code == 200

            # Wait for telemetry to flush
            time.sleep(4)

        events = _get_dependency_events(iast_test_token)
        assert len(events) > 0, "No app-dependencies-loaded events found"

        # Look for CVE-2024-35195 registered with reached=[]
        dep, cve_value = _find_dep_with_cve(events, "requests", "CVE-2024-35195")

        assert dep is not None, (
            f"CVE-2024-35195 not found in requests dependency metadata at load time. "
            f"Events: {json.dumps(events, indent=2)[:2000]}"
        )
        assert cve_value["id"] == "CVE-2024-35195"
        assert isinstance(cve_value["reached"], list)
        # reached should be empty — no vulnerable endpoint was called
        assert len(cve_value["reached"]) == 0, (
            f"Expected reached=[] for CVE registered at load time (no vulnerable call), got: {cve_value['reached']}"
        )
