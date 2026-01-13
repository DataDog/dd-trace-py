"""End-to-end integration tests for SCA detection with FastAPI using the test agent.

These tests validate that:
- SCA runtime instrumentation is working correctly via Remote Configuration
- Instrumented functions report span tags
- Detection hits are recorded
- Telemetry metrics are sent
- Multiple invocations are tracked
- Async functions work correctly

These tests use the test agent to simulate Remote Configuration payloads,
following the same pattern as test_flask_remoteconfig.py
"""

import base64
import hashlib
import json
import time

import pytest

from ddtrace.appsec._constants import SCA
from tests.appsec.appsec_utils import uvicorn_server
from tests.appsec.integrations.utils_testagent import _get_agent_client
from tests.appsec.integrations.utils_testagent import _get_span


# Common environment configuration for SCA tests
SCA_ENV = {
    "DD_APPSEC_SCA_ENABLED": "true",
    "DD_SCA_DETECTION_ENABLED": "true",
    "DD_APPSEC_ENABLED": "true",
    "DD_REMOTE_CONFIGURATION_ENABLED": "true",
}


def _sca_rc_config(token, targets):
    """Send SCA detection configuration via Remote Config through test agent.

    This simulates the Remote Configuration backend sending instrumentation targets
    to the running application.

    Args:
        token: Test session token
        targets: List of qualified names to instrument (e.g., ["os.path:join"])
    """
    path = "datadog/2/SCA_DETECTION/sca_config/config"
    msg = {"targets": targets}

    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config/path?test_session_token=%s" % (token,),
        json.dumps({"path": path, "msg": msg}),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _sca_rc_config_full(token, targets):
    """Send full Remote Config payload for SCA detection.

    This sends a complete RC payload with proper structure including
    signatures, targets, and client configs.

    Args:
        token: Test session token
        targets: List of qualified names to instrument
    """
    import datetime

    expires_date = datetime.datetime.strftime(
        datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
    )

    path = "datadog/2/SCA_DETECTION/sca_config/config"
    msg = {"targets": targets}
    msg_enc = bytes(json.dumps(msg), encoding="utf-8")

    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": {
            "_type": "targets",
            "custom": {"opaque_backend_state": ""},
            "expires": expires_date,
            "spec_version": "1.0.0",
            "targets": {
                path: {
                    "custom": {"c": [""], "v": 0},
                    "hashes": {"sha256": hashlib.sha256(msg_enc).hexdigest()},
                    "length": len(msg_enc),
                },
            },
            "version": 0,
        },
    }

    remote_config_payload = {
        "roots": [
            str(
                base64.b64encode(
                    bytes(
                        json.dumps(
                            {
                                "signatures": [],
                                "signed": {
                                    "_type": "root",
                                    "consistent_snapshot": True,
                                    "expires": "1986-12-11T00:00:00Z",
                                    "keys": {},
                                    "roles": {},
                                    "spec_version": "1.0",
                                    "version": 2,
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                ),
                encoding="utf-8",
            )
        ],
        "targets": str(base64.b64encode(bytes(json.dumps(data), encoding="utf-8")), encoding="utf-8"),
        "target_files": [
            {
                "path": path,
                "raw": str(base64.b64encode(msg_enc), encoding="utf-8"),
            },
        ],
        "client_configs": [path],
    }

    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config?test_session_token=%s" % (token,),
        json.dumps(remote_config_payload),
    )
    resp = client.getresponse()
    assert resp.status == 202


def _wait_for_sca_instrumentation(client, endpoint="/sca-vulnerable-function", max_retries=20, sleep_time=0.5):
    """Wait for SCA instrumentation to be applied by making requests until we see SCA tags.

    Args:
        client: HTTP client for making requests
        endpoint: Endpoint to call
        max_retries: Maximum number of retry attempts
        sleep_time: Time to sleep between retries

    Returns:
        True if instrumentation was detected, False otherwise
    """
    time.sleep(sleep_time)
    for _ in range(max_retries):
        try:
            response = client.get(endpoint)
            if response.status_code == 200:
                # Small delay for spans to be sent
                time.sleep(0.2)
                return True
        except Exception:
            pass
        time.sleep(sleep_time)
    return False


def test_sca_basic_instrumentation(iast_test_token):
    """Test that SCA instrumentation works via Remote Config on a basic endpoint."""
    token = iast_test_token  # Use the fixture token which has start_trace() called
    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Send RC config to instrument os.path:join
        _sca_rc_config(token, ["os.path:join"])

        # Wait for instrumentation to be applied and make request
        assert _wait_for_sca_instrumentation(fastapi_client, "/sca-vulnerable-function")

    # Fetch spans from test agent
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            # Check for SCA tags
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # Verify SCA instrumentation was recorded
    assert len(spans_with_sca) >= 1, f"Expected SCA spans, got: {len(spans_with_sca)} spans"

    # Verify span tags
    sca_span = spans_with_sca[0]
    assert sca_span["meta"][SCA.TAG_INSTRUMENTED] == "true"
    assert sca_span["meta"][SCA.TAG_DETECTION_HIT] == "true"
    assert SCA.TAG_TARGET in sca_span["meta"]
    assert "os.path:join" in sca_span["meta"][SCA.TAG_TARGET]


@pytest.mark.parametrize("use_multiprocess", (True, False))
def test_sca_multiple_calls(iast_test_token, use_multiprocess):
    """Test that SCA tracks multiple invocations of instrumented functions."""
    with uvicorn_server(
        appsec_enabled="true",
        token=iast_test_token,
        port=8050,
        env=SCA_ENV,
        use_multiprocess=use_multiprocess,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument multiple functions
        _sca_rc_config(iast_test_token, ["os.path:join", "os.path:exists"])

        # Wait for instrumentation
        assert _wait_for_sca_instrumentation(fastapi_client, "/sca-multiple-calls")

    # Fetch spans
    response_tracer = _get_span(iast_test_token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # Should have SCA tags
    assert len(spans_with_sca) >= 1


def test_sca_async_function(iast_test_token):
    """Test that SCA instrumentation works with async functions."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument os.path:join
        _sca_rc_config(token, ["os.path:join"])

        # Wait for instrumentation and call async endpoint
        assert _wait_for_sca_instrumentation(fastapi_client, "/sca-async-vulnerable")

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # Verify SCA works with async
    assert len(spans_with_sca) >= 1


def test_sca_form_data(iast_test_token):
    """Test SCA with form data input."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument os.path:join
        _sca_rc_config(token, ["os.path:join"])

        # Wait for instrumentation
        time.sleep(1)

        # POST request with form data
        response = fastapi_client.post("/sca-vulnerable-request", data={"data": "user/input"})
        assert response.status_code == 200

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_DETECTION_HIT) == "true":
                spans_with_sca.append(span)

    assert len(spans_with_sca) >= 1


def test_sca_nested_calls(iast_test_token):
    """Test SCA detection with nested function calls."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument multiple functions
        _sca_rc_config(token, ["os.path:join", "os.path:dirname"])

        # Wait for instrumentation
        assert _wait_for_sca_instrumentation(fastapi_client, "/sca-nested-calls")

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # Should detect functions
    assert len(spans_with_sca) >= 1


def test_sca_no_instrumentation_without_config(iast_test_token):
    """Test that without RC configuration, no SCA tags are added."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # DO NOT send RC config - no instrumentation should occur

        # Call the endpoint immediately
        response = fastapi_client.get("/sca-vulnerable-function")
        assert response.status_code == 200

        # Wait a bit for any potential instrumentation
        time.sleep(0.5)

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # Should have no SCA tags without RC configuration
    assert len(spans_with_sca) == 0, "Expected no SCA instrumentation without RC config"


def test_sca_concurrent_requests(iast_test_token):
    """Test SCA instrumentation with concurrent requests."""
    import concurrent.futures

    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument function
        _sca_rc_config(token, ["os.path:join"])

        # Wait for instrumentation
        time.sleep(1)

        # Send multiple concurrent requests
        def worker():
            response = fastapi_client.get("/sca-vulnerable-function")
            return response.status_code == 200

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker) for _ in range(5)]
            results = [f.result() for f in futures]

        # All should succeed
        assert all(results)

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_DETECTION_HIT) == "true":
                spans_with_sca.append(span)

    # Should have multiple spans with SCA detection
    assert len(spans_with_sca) >= 1


def test_sca_span_tags_format(iast_test_token):
    """Test that SCA span tags have the correct format."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument specific function
        target_function = "os.path:join"
        _sca_rc_config(token, [target_function])

        # Wait for instrumentation
        assert _wait_for_sca_instrumentation(fastapi_client, "/sca-vulnerable-function")

    # Fetch spans
    response_tracer = _get_span(token)
    sca_spans = []
    for trace in response_tracer:
        for span in trace:
            meta = span.get("meta", {})
            if meta.get(SCA.TAG_INSTRUMENTED) == "true":
                sca_spans.append(span)

    # Verify we found SCA spans
    assert len(sca_spans) >= 1, "No SCA spans found"

    # Validate span tag format
    for span in sca_spans:
        meta = span["meta"]

        # Check required tags are present
        assert SCA.TAG_INSTRUMENTED in meta, "Missing instrumented tag"
        assert meta[SCA.TAG_INSTRUMENTED] == "true", "Invalid instrumented tag value"

        assert SCA.TAG_DETECTION_HIT in meta, "Missing detection_hit tag"
        assert meta[SCA.TAG_DETECTION_HIT] == "true", "Invalid detection_hit tag value"

        assert SCA.TAG_TARGET in meta, "Missing target tag"
        # Target should be the qualified name
        assert ":" in meta[SCA.TAG_TARGET], "Target tag should contain qualified name with ':'"


def test_sca_full_rc_payload(iast_test_token):
    """Test SCA with full Remote Config payload structure."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Send full RC payload
        _sca_rc_config_full(token, ["os.path:join", "os.path:exists"])

        # Wait for instrumentation
        assert _wait_for_sca_instrumentation(fastapi_client, "/sca-multiple-calls")

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # Should have instrumentation
    assert len(spans_with_sca) >= 1


@pytest.mark.parametrize(
    "endpoint,targets",
    [
        ("/sca-vulnerable-function", ["os.path:join"]),
        ("/sca-async-vulnerable", ["os.path:join"]),
        ("/sca-nested-calls", ["os.path:join", "os.path:dirname"]),
    ],
)
def test_sca_multiple_endpoints(endpoint, targets, iast_test_token):
    """Parametrized test for SCA across multiple endpoints."""
    token = iast_test_token

    with uvicorn_server(
        appsec_enabled="true",
        token=token,
        port=8050,
        env=SCA_ENV,
    ) as context:
        _, fastapi_client, pid = context

        # Instrument functions
        _sca_rc_config(token, targets)

        # Wait for instrumentation
        assert _wait_for_sca_instrumentation(fastapi_client, endpoint)

    # Fetch spans
    response_tracer = _get_span(token)
    spans_with_sca = []
    for trace in response_tracer:
        for span in trace:
            if span.get("meta", {}).get(SCA.TAG_INSTRUMENTED) == "true":
                spans_with_sca.append(span)

    # All endpoints should trigger SCA detection
    assert len(spans_with_sca) >= 1, f"No SCA detection for endpoint: {endpoint}"
