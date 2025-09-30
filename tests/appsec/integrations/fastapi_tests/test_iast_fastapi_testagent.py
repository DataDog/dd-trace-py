import concurrent.futures
import json

from mock import ANY
import pytest
from requests.exceptions import ConnectionError  # noqa: A004

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_SSRF
from tests.appsec.appsec_utils import uvicorn_server
from tests.appsec.iast.iast_utils import load_iast_report
from tests.appsec.integrations.utils_testagent import _get_span


def test_iast_header_injection_secure_attack(iast_test_token):
    """Test that header injection is prevented in a secure FastAPI endpoint."""
    with uvicorn_server(
        iast_enabled="true", token=iast_test_token, port=8050, env={"DD_TRACE_DEBUG": "true"}
    ) as context:
        _, fastapi_client, pid = context
        with pytest.raises(ConnectionError):
            fastapi_client.get(
                "/iast-header-injection-vulnerability-secure",
                params={"header": "value\r\nStrict-Transport-Security: max-age=0"},
            )

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


def test_iast_header_injection(iast_test_token):
    """Test that header injection attempts are detected in FastAPI."""
    with uvicorn_server(
        iast_enabled="true", token=iast_test_token, port=8050, env={"DD_TRACE_DEBUG": "true"}
    ) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.get("/iast-header-injection-vulnerability", params={"header": "value"})

        assert response.status_code == 200
        assert response.headers["X-Vulnerable-Header"] == "value"

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


def test_iast_header_injection_attack(iast_test_token):
    """Test that header injection attempts are detected in FastAPI."""
    with uvicorn_server(
        iast_enabled="true", token=iast_test_token, port=8050, env={"DD_TRACE_DEBUG": "true"}
    ) as context:
        _, fastapi_client, pid = context
        with pytest.raises(ConnectionError):
            fastapi_client.get(
                "/iast-header-injection-vulnerability", params={"header": "value\r\nInjected-Header: 1234"}
            )

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


def test_iast_cmdi_uvicorn(iast_test_token):
    """Test command injection vulnerability detection with uvicorn server."""
    with uvicorn_server(
        iast_enabled="true", token=iast_test_token, port=8050, env={"DD_TRACE_DEBUG": "true"}
    ) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.get("/iast-cmdi-vulnerability", params={"filename": "path_traversal_test_file.txt"})
        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data and iast_data.get("vulnerabilities"):
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "ls "},
        {"redacted": True},
        {"pattern": "abcdefghijklmnopqrstuvwxyzAB", "redacted": True, "source": 0},
    ]
    assert vulnerability["hash"]


def test_iast_cmdi_form_request_fastapi(iast_test_token):
    """Validate IAST CMDI detection when FastAPI parses forms via request.form().

    Targets the endpoint /iast-cmdi-vulnerability-form-request which reads the form from the
    Request object rather than using Form(...) in the signature.
    """

    env = {
        "_DD_IAST_PATCH_MODULES": ("benchmarks.," "tests.appsec.," "tests.appsec.integrations.fastapi_tests.app."),
    }
    with uvicorn_server(iast_enabled="true", token=iast_test_token, port=8050, env=env) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.post(
            "/iast-cmdi-vulnerability-form-request",
            data={"command": "ls path_traversal_test_file.txt"},
        )
        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2, f"Invalid number of spans ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) == 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) == 1

    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"] == {
        "valueParts": [
            {"value": "ls "},
            {"redacted": True, "source": 0, "pattern": ANY},
        ]
    }
    assert vulnerability["hash"]


def test_iast_cmdi_form_multiple_fastapi(iast_test_token):
    """Validate IAST CMDI detection with multiple Form parameters in FastAPI.

    Targets the endpoint /iast-cmdi-vulnerability-form-multiple which declares two Form parameters
    (command and flag). The vulnerable value is "command".
    """

    env = {
        "_DD_IAST_PATCH_MODULES": ("benchmarks.," "tests.appsec.," "tests.appsec.integrations.fastapi_tests.app."),
    }
    with uvicorn_server(iast_enabled="true", token=iast_test_token, port=8050, env=env) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.post(
            "/iast-cmdi-vulnerability-form-multiple",
            data={"command": "path_traversal_test_file.txt", "flag": "-la"},
        )
        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2, f"Invalid number of spans ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) == 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) == 1

    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"] == {
        "valueParts": [
            {"value": "ls "},
            {"redacted": True, "source": 0, "pattern": ANY},
            {"redacted": True},
            {"redacted": True, "source": 1, "pattern": ANY},
        ]
    }
    assert vulnerability["hash"]


def test_iast_cmdi_form_uvicorn(iast_test_token):
    """Test command injection vulnerability detection with form data using uvicorn server."""
    with uvicorn_server(
        iast_enabled="true", token=iast_test_token, port=8050, env={"DD_TRACE_DEBUG": "true"}
    ) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.post(
            "/iast-cmdi-vulnerability-form", data={"command": "ls path_traversal_test_file.txt"}
        )
        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data and iast_data.get("vulnerabilities"):
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "ls "},
        {"redacted": True},
        {"pattern": "abcdefghijklmnopqrstuvwxyzABCDE", "redacted": True, "source": 0},
    ]
    assert vulnerability["hash"]


def test_iast_concurrent_requests_limit(iast_test_token):
    """Ensure only DD_IAST_MAX_CONCURRENT_REQUESTS requests have IAST enabled concurrently.

    This test hits the /iast-enabled endpoint concurrently. The endpoint awaits a short sleep
    to maximize overlap. The number of responses with enabled=True must equal the configured
    DD_IAST_MAX_CONCURRENT_REQUESTS.
    """
    max_concurrent = 7
    rejected_requests = 15
    total_requests = max_concurrent + rejected_requests
    delay_ms = 500

    env = {
        "DD_IAST_MAX_CONCURRENT_REQUESTS": str(max_concurrent),
    }

    with uvicorn_server(iast_enabled="true", token=iast_test_token, port=8050, env=env) as context:
        _, fastapi_client, pid = context

        def worker():
            r = fastapi_client.get("/iast-enabled", params={"delay_ms": delay_ms}, timeout=5)
            assert r.status_code == 200
            data = r.json()
            return bool(data.get("enabled"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=total_requests) as executor:
            futures = [executor.submit(worker) for _ in range(total_requests)]
            results = [f.result() for f in futures]

    # We don't need to fetch spans for this behavior validation; just assert the limit enforcement.
    true_count = results.count(True)
    false_count = results.count(False)
    assert true_count == max_concurrent
    assert false_count == rejected_requests


def test_iast_vulnerable_request_downstream_fastapi(iast_test_token):
    """Mirror downstream propagation test for FastAPI server.

    Sends a request with Datadog headers to the FastAPI endpoint which triggers a weak-hash
    vulnerability and then calls a downstream endpoint to echo headers. Asserts that headers are
    properly propagated and that an IAST WEAK_HASH vulnerability is reported.
    """
    env = {
        "DD_APM_TRACING_ENABLED": "false",
        "DD_TRACE_URLLIB3_ENABLED": "true",
    }
    with uvicorn_server(iast_enabled="true", token=iast_test_token, env=env, port=8050) as context:
        _, fastapi_client, pid = context

        trace_id = 1212121212121212121
        parent_id = 34343434
        response = fastapi_client.get(
            "/vulnerablerequestdownstream",
            params={"port": 8050},
            headers={
                "x-datadog-trace-id": str(trace_id),
                "x-datadog-parent-id": str(parent_id),
                "x-datadog-sampling-priority": "-1",
                "x-datadog-origin": "rum",
                "x-datadog-tags": "_dd.p.other=1",
            },
        )

        assert response.status_code == 200
        downstream_headers = json.loads(response.json())
        assert downstream_headers["x-datadog-origin"] == "rum"
        assert downstream_headers["x-datadog-parent-id"] != "34343434"
        assert "_dd.p.other=1" in downstream_headers["x-datadog-tags"]
        assert downstream_headers["x-datadog-sampling-priority"] == "2"
        assert downstream_headers["x-datadog-trace-id"] == "1212121212121212121"

    response_tracer = _get_span(iast_test_token)
    spans = []
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
            spans.append(span)

    assert len(spans) >= 6, f"Incorrect number of spans ({len(spans)}):\n{spans}"
    assert len(spans_with_iast) >= 2, f"Invalid number of spans with IAST ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) >= 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) >= 1
    for vulnerability in vulnerabilities[0]:
        assert vulnerability["type"] in {VULN_INSECURE_HASHING_TYPE, VULN_SSRF}
        assert vulnerability["hash"]


@pytest.mark.parametrize(
    "body, content_type",
    [
        ("master", "text/plain"),
        ('"master"', "application/json"),  # raw JSON string
        ('{"key":"master"}', "application/json"),  # simple JSON object
    ],
)
def test_iast_cmdi_bodies_fastapi(body, content_type, iast_test_token):
    """Parametrized body encodings to validate that IAST taints http.request.body in FastAPI
    and still reports CMDI on the vulnerable sink in tests/appsec/integrations/fastapi_tests/app.py:cmdi_body
    """
    env = {
        "_DD_IAST_PATCH_MODULES": ("benchmarks.," "tests.appsec.," "tests.appsec.integrations.fastapi_tests.app."),
    }
    with uvicorn_server(iast_enabled="true", token=iast_test_token, port=8050, env=env) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.post(
            "/iast-cmdi-vulnerability-body",
            data=body,
            headers={"Content-Type": content_type},
        )

        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) == 2, f"Invalid number of spans ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) == 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) == 1

    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"] == {
        "valueParts": [
            {"value": "ls "},
            {"redacted": True, "source": 0, "pattern": ANY},
        ]
    }
    assert vulnerability["hash"]


@pytest.mark.parametrize(
    "body, content_type",
    [
        ("", "text/plain"),
        ('["master","ignore"]', "application/json"),  # JSON array
        ('{"first":"ignore","second":"master"}', "application/json"),  # multi-key object
        ("", "application/json"),  # JSON array
        ("[]", "application/json"),  # JSON array
        ("[{}]", "application/json"),  # JSON array
    ],
)
def test_iast_cmdi_bodies_fastapi_no_vulnerabilities(body, content_type, iast_test_token):
    """Parametrized body encodings to validate that IAST taints http.request.body in FastAPI
    and still reports CMDI on the vulnerable sink in tests/appsec/integrations/fastapi_tests/app.py:cmdi_body
    """

    env = {
        "_DD_IAST_PATCH_MODULES": ("benchmarks.," "tests.appsec.," "tests.appsec.integrations.fastapi_tests.app."),
    }
    with uvicorn_server(iast_enabled="true", token=iast_test_token, port=8050, env=env) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.post(
            "/iast-cmdi-vulnerability-body",
            data=body,
            headers={"Content-Type": content_type},
        )

        assert response.status_code in [200, 500]

    response_tracer = _get_span(iast_test_token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))

    assert len(spans_with_iast) >= 0, f"Invalid number of spans ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) == 0, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
