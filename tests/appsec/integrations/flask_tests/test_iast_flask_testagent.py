import json

import pytest

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from tests.appsec.appsec_utils import flask_server
from tests.appsec.appsec_utils import gunicorn_server
from tests.appsec.iast.iast_utils import load_iast_report
from tests.appsec.integrations.flask_tests.utils import flask_version
from tests.appsec.integrations.utils_testagent import _get_span
from tests.appsec.integrations.utils_testagent import clear_session
from tests.appsec.integrations.utils_testagent import start_trace


@pytest.mark.skip(reason="Stacktrace error in debug mode doesn't work outside the request APPSEC-56862")
def test_iast_stacktrace_error():
    token = "test_iast_stacktrace_error"
    _ = start_trace(token)
    with flask_server(iast_enabled="true", token=token, port=8050, env={"FLASK_DEBUG": "true"}) as context:
        _, flask_client, pid = context
        response = flask_client.get(
            "/iast-stacktrace-leak-vulnerability", headers={"X-Datadog-Test-Session-Token": token}
        )
        assert response.status_code == 500
        assert (
            b"<title>ValueError: Check my stacktrace!" in response.content
        ), f"Exception doesn't found in CONTENT: {response.content}"

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_STACKTRACE_LEAK
    assert vulnerability["evidence"]["valueParts"][0]["value"].startswith("Module: ")
    assert "path" not in vulnerability["location"]
    assert "line" not in vulnerability["location"]
    assert vulnerability["hash"]


@pytest.mark.parametrize("server", (gunicorn_server, flask_server))
def test_iast_cmdi(server):
    token = "test_iast_cmdi"
    _ = start_trace(token)
    with server(iast_enabled="true", token=token, port=8050, use_ddtrace_cmd=False) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability?filename=path_traversal_test_file.txt")

        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
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


@pytest.mark.parametrize("server", (gunicorn_server, flask_server))
def test_iast_cmdi_secure(server):
    token = "test_iast_cmdi_secure"
    _ = start_trace(token)
    with server(iast_enabled="true", token=token, port=8050, use_ddtrace_cmd=False) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability-secure?filename=path_traversal_test_file.txt")

        assert response.status_code == 200

    response_tracer = _get_span(token)
    for trace in response_tracer:
        for span in trace:
            iast_data = load_iast_report(span)
            if iast_data:
                pytest.fail(f"There is iast vulnerabilities: {iast_data}")
    clear_session(token)


@pytest.mark.parametrize("server", (gunicorn_server, flask_server))
def test_iast_header_injection_secure(server):
    """Test that header injection is prevented in a real Flask application.

    This test demonstrates the secure behavior of Flask's header handling in a real application
    environment. When setting headers using response.headers['Header-Name'] = value, Flask uses
    Werkzeug's header handling mechanism which calls werkzeug.datastructures.headers._str_header_value
    internally. This method sanitizes header values and prevents header injection attacks by:

    1. Validating the header value format
    2. Removing or escaping potentially dangerous characters
    3. Raising exceptions for invalid header values

    This is in contrast to the test client behavior (test_flask_header_injection) where header
    values can be set directly without going through Werkzeug's sanitization.
    """
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with server(iast_enabled="true", token=token, port=8050, use_ddtrace_cmd=False) as context:
        _, flask_client, pid = context

        response = flask_client.get(
            "/iast-header-injection-vulnerability-secure?header=value%0d%0aStrict-Transport-Security%3a+max-age%3d0%0"
        )

        assert response.status_code == 200
        assert response.headers["X-Vulnerable-Header"] == "param=value%0D%0AStrict-Transport-Security%3A max-age%3D0%0"
        assert response.headers.get("Strict-Transport-Security") is None

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 0


@pytest.mark.parametrize("server", ((gunicorn_server, flask_server)))
def test_iast_header_injection(server):
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with server(iast_enabled="true", token=token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.post(
            "/iast-header-injection-vulnerability", data={"header": "value\r\nInject-Header: 1234"}
        )

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
    clear_session(token)

    if flask_version > (1, 2):
        assert response.status_code == 500
        assert response.headers.get("X-Vulnerable-Header") is None
        assert response.headers.get("Inject-Header") is None
        assert len(spans_with_iast) == 2
        assert len(vulnerabilities) == 0
    else:
        if server.__name__ == "flask_server":
            assert response.status_code == 200
            assert response.headers.get("X-Vulnerable-Header") == "value"
            assert response.headers.get("Inject-Header") == "1234"
            assert len(spans_with_iast) == 2
            assert len(vulnerabilities) == 0
        else:
            assert response.status_code == 400
            assert response.headers.get("X-Vulnerable-Header") is None
            assert response.headers.get("Inject-Header") is None
            assert len(spans_with_iast) == 2
            assert len(vulnerabilities) == 0


@pytest.mark.parametrize("server", ((gunicorn_server, flask_server)))
def test_iast_code_injection_with_stacktrace(server):
    token = "test_iast_code_injection_with_stacktrace"
    _ = start_trace(token)
    tainted_string = "code_injection_string"
    with server(iast_enabled="true", token=token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get(f"/iast-code-injection?filename={tainted_string}")

        assert response.status_code == 200
        assert response.content == b"OK:v0.4:" + bytes(tainted_string, "utf-8")
    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    metastruct = {}
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                metastruct = span.get("meta_struct", {})
                vulnerabilities.append(iast_data.get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CODE_INJECTION
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "a + '"},
        {"value": tainted_string, "source": 0},
        {"value": "'"},
    ]
    assert vulnerability["hash"]
    assert metastruct


def test_iast_unvalidated_redirect():
    token = "test_iast_cmdi"
    _ = start_trace(token)
    with gunicorn_server(iast_enabled="true", token=token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-unvalidated_redirect-header?location=malicious_url")

        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
                spans_with_iast.append(span)
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
    clear_session(token)

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_UNVALIDATED_REDIRECT
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": "malicious_url"}]
    assert vulnerability["hash"]


@pytest.mark.parametrize(
    "server, config",
    (
        (
            gunicorn_server,
            {
                "workers": "3",
                "use_threads": False,
                "use_gevent": False,
                "env": {
                    "DD_APM_TRACING_ENABLED": "false",
                },
            },
        ),
        (
            gunicorn_server,
            {
                "workers": "3",
                "use_threads": True,
                "use_gevent": False,
                "env": {
                    "DD_APM_TRACING_ENABLED": "false",
                },
            },
        ),
        (
            gunicorn_server,
            {
                "workers": "3",
                "use_threads": True,
                "use_gevent": True,
                "env": {
                    "DD_APM_TRACING_ENABLED": "false",
                },
            },
        ),
        (
            gunicorn_server,
            {
                "workers": "1",
                "use_threads": True,
                "use_gevent": True,
                "env": {
                    "DD_APM_TRACING_ENABLED": "false",
                    "_DD_IAST_PROPAGATION_ENABLED": "false",
                },
            },
        ),
        (
            gunicorn_server,
            {
                "workers": "1",
                "use_threads": True,
                "use_gevent": True,
                "env": {
                    "DD_APM_TRACING_ENABLED": "false",
                },
            },
        ),
        (
            gunicorn_server,
            {
                "workers": "1",
                "use_threads": True,
                "use_gevent": True,
                "env": {"_DD_IAST_PROPAGATION_ENABLED": "false"},
            },
        ),
        (flask_server, {"env": {"DD_APM_TRACING_ENABLED": "false"}}),
    ),
)
def test_iast_vulnerable_request_downstream(server, config):
    """Gevent has a lot of problematic interactions with the tracer. When IAST applies AST transformations to a file
    and reloads the module using compile and exec, it can interfere with Gevent’s monkey patching
    """
    token = "test_iast_vulnerable_request_downstream"
    _ = start_trace(token)
    with server(iast_enabled="true", token=token, port=8050, **config) as context:
        _, flask_client, pid = context
        trace_id = 1212121212121212121
        parent_id = 34343434
        response = flask_client.get(
            "/vulnerablerequestdownstream",
            headers={
                "x-datadog-trace-id": str(trace_id),
                "x-datadog-parent-id": str(parent_id),
                "x-datadog-sampling-priority": "-1",
                "x-datadog-origin": "rum",
                "x-datadog-tags": "_dd.p.other=1",
            },
        )

        assert response.status_code == 200
        downstream_headers = json.loads(response.text)

        assert downstream_headers["X-Datadog-Origin"] == "rum"
        assert downstream_headers["X-Datadog-Parent-Id"] != "34343434"
        assert "_dd.p.other=1" in downstream_headers["X-Datadog-Tags"]
        assert downstream_headers["X-Datadog-Sampling-Priority"] == "2"
        assert downstream_headers["X-Datadog-Trace-Id"] == "1212121212121212121"

    response_tracer = _get_span(token)
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
    clear_session(token)

    assert len(spans) >= 28, f"Incorrect number of spans ({len(spans)}):\n{spans}"
    assert len(spans_with_iast) == 3
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_INSECURE_HASHING_TYPE
    assert "valueParts" not in vulnerability["evidence"]
    assert vulnerability["hash"]
