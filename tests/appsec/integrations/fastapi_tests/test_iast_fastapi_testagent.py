import json

import pytest
from requests.exceptions import ConnectionError  # noqa: A004

from ddtrace.appsec._iast.constants import VULN_CMDI
from tests.appsec.appsec_utils import uvicorn_server
from tests.appsec.integrations.utils_testagent import _get_span
from tests.appsec.integrations.utils_testagent import clear_session
from tests.appsec.integrations.utils_testagent import start_trace


def test_iast_header_injection_secure_attack():
    """Test that header injection is prevented in a secure FastAPI endpoint."""
    token = "test_iast_header_injection_secure"
    _ = start_trace(token)
    with uvicorn_server(iast_enabled="true", token=token, port=8050, env={"DD_TRACE_DEBUG": "true"}) as context:
        _, fastapi_client, pid = context
        with pytest.raises(ConnectionError):
            fastapi_client.get(
                "/iast-header-injection-vulnerability-secure",
                params={"header": "value\r\nStrict-Transport-Security: max-age=0"},
            )

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


def test_iast_header_injection():
    """Test that header injection attempts are detected in FastAPI."""
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with uvicorn_server(iast_enabled="true", token=token, port=8050, env={"DD_TRACE_DEBUG": "true"}) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.get("/iast-header-injection-vulnerability", params={"header": "value"})

        assert response.status_code == 200
        assert response.headers["X-Vulnerable-Header"] == "value"

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


def test_iast_header_injection_attack():
    """Test that header injection attempts are detected in FastAPI."""
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with uvicorn_server(iast_enabled="true", token=token, port=8050, env={"DD_TRACE_DEBUG": "true"}) as context:
        _, fastapi_client, pid = context
        with pytest.raises(ConnectionError):
            fastapi_client.get(
                "/iast-header-injection-vulnerability", params={"header": "value\r\nInjected-Header: 1234"}
            )

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


def test_iast_cmdi_uvicorn():
    """Test command injection vulnerability detection with uvicorn server."""
    token = "test_iast_cmdi_uvicorn"
    _ = start_trace(token)
    with uvicorn_server(iast_enabled="true", token=token, port=8050, env={"DD_TRACE_DEBUG": "true"}) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.get("/iast-cmdi-vulnerability", params={"filename": "path_traversal_test_file.txt"})
        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)

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


def test_iast_cmdi_form_uvicorn():
    """Test command injection vulnerability detection with form data using uvicorn server."""
    token = "test_iast_cmdi_form_uvicorn"
    _ = start_trace(token)
    with uvicorn_server(iast_enabled="true", token=token, port=8050, env={"DD_TRACE_DEBUG": "true"}) as context:
        _, fastapi_client, pid = context

        response = fastapi_client.post(
            "/iast-cmdi-vulnerability-form", data={"command": "ls path_traversal_test_file.txt"}
        )
        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)

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
