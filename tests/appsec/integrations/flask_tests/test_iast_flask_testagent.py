import concurrent.futures
import json
import sys

import pytest

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from tests.appsec.appsec_utils import flask_server
from tests.appsec.appsec_utils import gunicorn_flask_server
from tests.appsec.iast.iast_utils import load_iast_report
from tests.appsec.integrations.flask_tests.utils import flask_version
from tests.appsec.integrations.utils_testagent import _get_span


_GEVENT_SERVERS_SCENARIOS = (
    (
        gunicorn_flask_server,
        {"workers": "3", "use_threads": False, "use_gevent": False, "env": {}},
    ),
    (
        gunicorn_flask_server,
        {"workers": "3", "use_threads": True, "use_gevent": False, "env": {}},
    ),
    (
        gunicorn_flask_server,
        {"workers": "3", "use_threads": True, "use_gevent": True, "env": {}},
    ),
    (
        gunicorn_flask_server,
        {
            "workers": "1",
            "use_threads": True,
            "use_gevent": True,
            "env": {
                "_DD_IAST_PROPAGATION_ENABLED": "false",
            },
        },
    ),
    (
        gunicorn_flask_server,
        {"workers": "1", "use_threads": True, "use_gevent": True, "env": {}},
    ),
    (
        gunicorn_flask_server,
        {
            "workers": "1",
            "use_threads": True,
            "use_gevent": True,
            "env": {"_DD_IAST_PROPAGATION_ENABLED": "false"},
        },
    ),
    (flask_server, {"env": {}}),
)


@pytest.mark.skip(reason="Stacktrace error in debug mode doesn't work outside the request APPSEC-56862")
def test_iast_stacktrace_error(iast_test_token):
    with flask_server(iast_enabled="true", token=iast_test_token, port=8050, env={"FLASK_DEBUG": "true"}) as context:
        _, flask_client, pid = context
        response = flask_client.get(
            "/iast-stacktrace-leak-vulnerability", headers={"X-Datadog-Test-Session-Token": iast_test_token}
        )
        assert response.status_code == 500
        assert (
            b"<title>ValueError: Check my stacktrace!" in response.content
        ), f"Exception doesn't found in CONTENT: {response.content}"

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
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_STACKTRACE_LEAK
    assert vulnerability["evidence"]["valueParts"][0]["value"].startswith("Module: ")
    assert "path" not in vulnerability["location"]
    assert "line" not in vulnerability["location"]
    assert vulnerability["hash"]


# TODO(APPSEC-59081): this test fails for every configuration (IAST enable/disable, Appsec enable/disable) so
#  the problem is related to the trace lifecycle
# @pytest.mark.parametrize(
#     "server, config",
#     (
#         (
#             gunicorn_flask_server,
#             {
#                 "workers": "1",
#                 "use_threads": True,
#                 "use_gevent": True,
#                 "env": {
#                     "DD_APM_TRACING_ENABLED": "true",
#                     "DD_TRACE_URLLIB3_ENABLED": "true",
#                 },
#             },
#         ),
#         (flask_server, {"env": {"DD_APM_TRACING_ENABLED": "true"}}),
#     ),
# )
# def test_iast_vulnerable_request_downstream_parallel(server, config):
#     """Run the vulnerable_request_downstream scenario many times in parallel.
#     """
#     # Keep a moderate fan-out to avoid overloading CI machines
#     fan_out = int(os.environ.get("TEST_PARALLEL_RUNS", "4"))
#     base_port = int(os.environ.get("TEST_PARALLEL_BASE_PORT", "8110"))
#
#     def run_one(idx: int):
#         tok = f"test_iast_vulnerable_request_downstream_parallel_{idx}"
#         _ = start_trace(tok)
#         port = base_port + idx
#         with server(iast_enabled="false", token=tok, port=port, **config) as context:
#             _, flask_client, pid = context
#             trace_id = 1212121212121212121
#             parent_id = 34343434
#             response = flask_client.get(
#                 f"/vulnerablerequestdownstream?port={port}",
#                 headers={
#                     "x-datadog-trace-id": str(trace_id),
#                     "x-datadog-parent-id": str(parent_id),
#                     "x-datadog-sampling-priority": "-1",
#                     "x-datadog-origin": "rum",
#                     "x-datadog-tags": "_dd.p.other=1",
#                 },
#             )
#
#             assert response.status_code == 200
#             # downstream_headers = json.loads(response.text)
#
#             # assert downstream_headers["X-Datadog-Origin"] == "rum"
#             # assert downstream_headers["X-Datadog-Parent-Id"] != "34343434"
#             # assert "_dd.p.other=1" in downstream_headers["X-Datadog-Tags"]
#             # assert downstream_headers["X-Datadog-Sampling-Priority"] == "2"
#             # assert downstream_headers["X-Datadog-Trace-Id"] == "1212121212121212121"
#
#         response_tracer = _get_span(tok)
#         spans = []
#         spans_with_iast = []
#         vulnerabilities = []
#
#         for trace in response_tracer:
#             for span in trace:
#                 if span.get("metrics", {}).get("_dd.iast.enabled") == 1.0:
#                     spans_with_iast.append(span)
#                 iast_data = load_iast_report(span)
#                 if iast_data:
#                     vulnerabilities.append(iast_data.get("vulnerabilities"))
#                 spans.append(span)
#         clear_session(tok)
#
#         assert len(spans) >= 28, f"Incorrect number of spans ({len(spans)}):\n{spans}"
#         # assert len(spans_with_iast) == 3
#         # assert len(vulnerabilities) == 1
#         # assert len(vulnerabilities[0]) == 2
#         # vulnerability = vulnerabilities[0][0]
#         # assert vulnerability["type"] == VULN_INSECURE_HASHING_TYPE
#         # assert "valueParts" not in vulnerability["evidence"]
#         # assert vulnerability["hash"]
#
#     with concurrent.futures.ThreadPoolExecutor(max_workers=fan_out) as ex:
#         list(ex.map(run_one, range(fan_out)))


@pytest.mark.parametrize("server", (gunicorn_flask_server, flask_server))
def test_iast_concurrent_requests_limit_flask(server, iast_test_token):
    """Ensure only DD_IAST_MAX_CONCURRENT_REQUESTS requests have IAST enabled concurrently.

    This mirrors the FastAPI test by hitting /iast-enabled concurrently on the Flask app.
    """
    max_concurrent = 7
    rejected_requests = 15
    total_requests = max_concurrent + rejected_requests
    delay_ms = 500

    env = {
        "DD_IAST_MAX_CONCURRENT_REQUESTS": str(max_concurrent),
    }

    with server(iast_enabled="true", token=iast_test_token, port=8050, env=env) as context:
        _, client, pid = context

        def worker():
            r = client.get(f"/iast-enabled?delay_ms={delay_ms}")
            assert r.status_code == 200
            # Flask endpoint returns a plain text 'true'/'false'
            return r.text.strip().lower() == "true"

        with concurrent.futures.ThreadPoolExecutor(max_workers=total_requests) as executor:
            futures = [executor.submit(worker) for _ in range(total_requests)]
            results = [f.result() for f in futures]

    true_count = results.count(True)
    false_count = results.count(False)
    if server.__name__ == "flask_server":
        assert true_count == max_concurrent
        assert false_count == rejected_requests
    else:
        # That is the correct result, Gunicorn has 1 worker (by default) and it processes all requests one by one
        assert true_count == total_requests
        assert false_count == 0


@pytest.mark.parametrize("server", (gunicorn_flask_server, flask_server))
def test_iast_cmdi(server, iast_test_token):
    with server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability?filename=path_traversal_test_file.txt")

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


@pytest.mark.parametrize("server", (gunicorn_flask_server, flask_server))
def test_iast_cmdi_secure(server, iast_test_token):
    with server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability-secure?filename=path_traversal_test_file.txt")

        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    for trace in response_tracer:
        for span in trace:
            iast_data = load_iast_report(span)
            if iast_data:
                pytest.fail(f"There is iast vulnerabilities: {iast_data}")


@pytest.mark.parametrize("server", (gunicorn_flask_server, flask_server))
def test_iast_sqli_complex(server, iast_test_token):
    """Test complex SQL injection detection with SQLAlchemy in a Flask application.

    This test verifies that IAST can properly detect SQL injection in complex SQLAlchemy
    queries, including those using CASE expressions, within a Flask application context.

    The test was added to catch a specific issue where SQLAlchemy's internal objects
    would raise a SystemError when their __repr__ was called during string formatting
    operations in the IAST taint tracking system.

    The error occurred because some SQLAlchemy objects (like those in CASE expressions)
    have a __repr__ method that can raise exceptions in certain contexts. The IAST system
    now handles these cases gracefully to prevent the error:

        SystemError: <slot wrapper '__repr__' of 'object' objects> returned a result with an exception set

    This test ensures that the fix remains effective in a real Flask application context.
    """

    with server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-sqli-vulnerability-complex")

        assert response.status_code == 200

    response_tracer = _get_span(iast_test_token)
    for trace in response_tracer:
        for span in trace:
            iast_data = load_iast_report(span)
            if iast_data:
                pytest.fail(f"There is iast vulnerabilities: {iast_data}")


@pytest.mark.parametrize("server", (gunicorn_flask_server, flask_server))
def test_iast_header_injection_secure(server, iast_test_token):
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
    with server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get(
            "/iast-header-injection-vulnerability-secure?header=value%0d%0aStrict-Transport-Security%3a+max-age%3d0%0"
        )

        assert response.status_code == 200
        assert response.headers["X-Vulnerable-Header"] == "param=value%0D%0AStrict-Transport-Security%3A max-age%3D0%0"
        assert response.headers.get("Strict-Transport-Security") is None

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


@pytest.mark.parametrize("server", ((gunicorn_flask_server, flask_server)))
def test_iast_header_injection(server, iast_test_token):
    with server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.post(
            "/iast-header-injection-vulnerability", data={"header": "value\r\nInject-Header: 1234"}
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


@pytest.mark.parametrize("server", ((gunicorn_flask_server, flask_server)))
def test_iast_code_injection_with_stacktrace(server, iast_test_token):
    tainted_string = "code_injection_string"
    with server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get(f"/iast-code-injection?filename={tainted_string}")

        assert response.status_code == 200
        assert response.content == b"OK:v0.4:" + bytes(tainted_string, "utf-8")
    response_tracer = _get_span(iast_test_token)
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


def test_iast_unvalidated_redirect(iast_test_token):
    with gunicorn_flask_server(iast_enabled="true", token=iast_test_token, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-unvalidated_redirect-header?location=malicious_url")

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

    assert len(spans_with_iast) == 2
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_UNVALIDATED_REDIRECT
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": "malicious_url"}]
    assert vulnerability["hash"]


@pytest.mark.parametrize("server, config", _GEVENT_SERVERS_SCENARIOS)
@pytest.mark.parametrize("apm_tracing_enabled", ("true", "false"))
def test_iast_vulnerable_request_downstream(server, config, apm_tracing_enabled, iast_test_token):
    """Gevent has a lot of problematic interactions with the tracer. When IAST applies AST transformations to a file
    and reloads the module using compile and exec, it can interfere with Gevent’s monkey patching
    """
    config["env"].update({"DD_TRACE_URLLIB3_ENABLED": "true"})
    with server(
        iast_enabled="true",
        appsec_enabled="false",
        apm_tracing_enabled=apm_tracing_enabled,
        token=iast_test_token,
        port=8050,
        **config,
    ) as context:
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

    assert len(spans) >= 28, f"Incorrect number of spans ({len(spans)}):\n{spans}"
    assert len(spans_with_iast) == 3
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    for vulnerability in vulnerabilities[0]:
        assert vulnerability["type"] in {VULN_INSECURE_HASHING_TYPE, VULN_SSRF}
        assert vulnerability["hash"]


@pytest.mark.parametrize("server, config", _GEVENT_SERVERS_SCENARIOS)
@pytest.mark.parametrize("iast_enabled", ("true", "false"))
def test_gevent_sensitive_socketpair(server, config, iast_enabled, iast_test_token):
    """Validate socket.socketpair lifecycle under various Gunicorn/gevent configurations."""
    with server(
        iast_enabled=iast_enabled, appsec_enabled="false", token=iast_test_token, port=8050, **config
    ) as context:
        _, flask_client, pid = context
        response = flask_client.get("/socketpair")
        assert response.status_code == 200
        assert response.text == "OK:True"


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Test not compatible with Python 3.8")
@pytest.mark.parametrize("server, config", _GEVENT_SERVERS_SCENARIOS)
@pytest.mark.parametrize("iast_enabled", ("true", "false"))
def test_gevent_sensitive_greenlet(server, config, iast_enabled, iast_test_token):
    """Validate gevent Greenlet execution under various Gunicorn/gevent configurations."""
    with server(
        iast_enabled=iast_enabled, appsec_enabled="false", token=iast_test_token, port=8050, **config
    ) as context:
        _, flask_client, pid = context
        response = flask_client.get("/gevent-greenlet")
        assert response.status_code == 200
        assert response.text == "OK:True"


@pytest.mark.parametrize("server, config", _GEVENT_SERVERS_SCENARIOS)
@pytest.mark.parametrize("iast_enabled", ("true", "false"))
def test_gevent_sensitive_subprocess(server, config, iast_enabled, iast_test_token):
    """Validate subprocess.Popen lifecycle under various Gunicorn/gevent configurations."""
    with server(
        iast_enabled=iast_enabled, appsec_enabled="false", token=iast_test_token, port=8050, **config
    ) as context:
        _, flask_client, pid = context
        response = flask_client.get("/subprocess-popen")
        assert response.status_code == 200
        assert response.text == "OK:True"
