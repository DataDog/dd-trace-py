import concurrent.futures

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.internal.logger import get_logger
from tests.appsec.appsec_utils import django_server
from tests.appsec.iast.iast_utils import load_iast_report
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
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
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


def test_iast_concurrent_requests_limit_django():
    """Ensure only DD_IAST_MAX_CONCURRENT_REQUESTS requests have IAST enabled concurrently in Django app.

    Hits /iast-enabled concurrently; the response contains whether IAST was enabled.
    The number of True responses must equal the configured max concurrent requests.
    """
    token = "test_iast_concurrent_requests_limit_django"
    _ = start_trace(token)

    max_concurrent = 7
    rejected_requests = 15
    total_requests = max_concurrent + rejected_requests
    delay_ms = 400

    env = {
        "DD_IAST_MAX_CONCURRENT_REQUESTS": str(max_concurrent),
    }

    with django_server(iast_enabled="true", token=token, env=env) as context:
        _, django_client, pid = context

        def worker():
            r = django_client.get(f"/iast-enabled/?delay_ms={delay_ms}", timeout=5)
            assert r.status_code == 200
            data = r.json()
            return bool(data.get("enabled"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=total_requests) as executor:
            futures = [executor.submit(worker) for _ in range(total_requests)]
            results = [f.result() for f in futures]

    true_count = results.count(True)
    false_count = results.count(False)
    assert true_count == max_concurrent, f"{len(results)} requests. Expected {max_concurrent}, got {true_count}"
    assert false_count == rejected_requests, f"{len(results)} requests. Expected {rejected_requests}, got {false_count}"


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
            iast_data = load_iast_report(span)
            if iast_data:
                vulnerabilities.append(iast_data.get("vulnerabilities"))
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
