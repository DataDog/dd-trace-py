import json

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.internal.logger import get_logger
from tests.appsec.appsec_utils import django_server
from tests.appsec.integrations.utils_testagent import _get_span
from tests.appsec.integrations.utils_testagent import clear_session
from tests.appsec.integrations.utils_testagent import start_trace


log = get_logger(__name__)


def test_iast_cmdi():
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with django_server(
        iast_enabled="true",
        token=token,
        env={
            "DD_TRACE_DEBUG": "true",
            "_DD_IAST_DEBUG": "true",
            "_DD_IAST_PATCH_MODULES": (
                "benchmarks.," "tests.appsec.," "tests.appsec.integrations.django_tests.django_app.views."
            ),
        },
    ) as context:
        _, django_client, pid = context

        response = django_client.post("/appsec/command-injection/", data="master")

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

    assert len(spans_with_iast) == 2, f"Invalid number of spans ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) == 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) == 1

    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"] == {
        "valueParts": [{"value": "dir "}, {"redacted": True}, {"redacted": True, "source": 0, "pattern": "abcdef"}]
    }
    assert vulnerability["location"]["spanId"]
    assert not vulnerability["location"]["path"].startswith("werkzeug")
    assert vulnerability["location"]["stackId"]
    assert vulnerability["hash"]


def test_iast_header_injection():
    token = "test_iast_header_injection"
    _ = start_trace(token)
    with django_server(
        iast_enabled="true",
        token=token,
        env={
            "DD_TRACE_DEBUG": "true",
            "_DD_IAST_DEBUG": "true",
            "_DD_IAST_PATCH_MODULES": (
                "benchmarks.," "tests.appsec.," "tests.appsec.integrations.django_tests.django_app.views."
            ),
        },
    ) as context:
        _, django_client, pid = context

        response = django_client.post("/appsec/header-injection/", data="master\r\nInjected-Header: 1234")

        assert response.status_code == 200
        assert response.content == b"OK:master\r\nInjected-Header: 1234", f"Content is {response.content}"
        assert response.headers["Header-Injection"] == "master"
        assert response.headers["Injected-Header"] == "1234"

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

    assert len(spans_with_iast) == 2, f"Invalid number of spans ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) == 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) == 1

    vulnerability = vulnerabilities[0][0]
    assert vulnerability["type"] == VULN_HEADER_INJECTION
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "Header-Injection: "},
        {"value": "master\r\nInjected-Header: 1234", "source": 0},
    ]
    assert vulnerability["location"]["spanId"]
    assert vulnerability["location"]["stackId"]
    assert vulnerability["hash"]
