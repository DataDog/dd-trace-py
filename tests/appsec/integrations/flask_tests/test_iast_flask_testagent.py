import json

import pytest

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from tests.appsec.appsec_utils import flask_server
from tests.appsec.integrations.flask_tests.test_flask_remoteconfig import _get_agent_client


def start_trace(token):
    client = _get_agent_client()
    client.request(
        "GET", "/test/session/start?test_session_token=%s" % (token,), headers={"X-Datadog-Test-Session-Token": token}
    )
    resp = client.getresponse()
    return resp.read()


def clear_session(token):
    client = _get_agent_client()
    client.request(
        "GET", "/test/session/clear?test_session_token=%s" % (token,), headers={"X-Datadog-Test-Session-Token": token}
    )
    resp = client.getresponse()
    return resp.read()


def _get_span(token):
    client = _get_agent_client()
    client.request(
        "GET", "/test/session/traces?test_session_token=%s" % (token,), headers={"X-Datadog-Test-Session-Token": token}
    )
    resp = client.getresponse()
    return json.loads(resp.read())


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
            if span.get("metrics", {}).get("_dd.iast.enabled"):
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_STACKTRACE_LEAK
    assert vulnerability["evidence"]["valueParts"][0]["value"].startswith("Module: ")
    assert "path" not in vulnerability["location"]
    assert "line" not in vulnerability["location"]
    assert vulnerability["hash"]


def test_iast_cmdi():
    token = "test_iast_cmdi"
    _ = start_trace(token)
    with flask_server(
        iast_enabled="true", token=token, port=8050, env={"FLASK_DEBUG": "true", "DD_TRACE_DEBUG": "true"}
    ) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability?filename=path_traversal_test_file.txt")

        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled"):
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)
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


def test_iast_cmdi_secure():
    token = "test_iast_cmdi_secure"
    _ = start_trace(token)
    with flask_server(iast_enabled="true", token=token, port=8050, env={"FLASK_DEBUG": "true"}) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability-secure?filename=path_traversal_test_file.txt")

        assert response.status_code == 200

    response_tracer = _get_span(token)
    for trace in response_tracer:
        for span in trace:
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                pytest.fail(f"There is iast vulnerabilities: {iast_data}")
    clear_session(token)


def test_iast_header_injection():
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with flask_server(
        iast_enabled="true", token=token, port=8050, env={"FLASK_DEBUG": "true", "DD_TRACE_DEBUG": "true"}
    ) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-header-injection-vulnerability?header=header_injection_param")

        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    for trace in response_tracer:
        for span in trace:
            if span.get("metrics", {}).get("_dd.iast.enabled"):
                spans_with_iast.append(span)
            iast_data = span["meta"].get("_dd.iast.json")
            if iast_data:
                vulnerabilities.append(json.loads(iast_data).get("vulnerabilities"))
    clear_session(token)
    assert len(vulnerabilities) == 1
    assert len(vulnerabilities[0]) == 1
    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_HEADER_INJECTION
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "Header-Injection: "},
        {"value": "header_injection_param", "source": 0},
    ]
    assert vulnerability["location"]["spanId"]
    assert not vulnerability["location"]["path"].startswith("werkzeug")
    assert vulnerability["location"]["stackId"]
    assert vulnerability["hash"]
