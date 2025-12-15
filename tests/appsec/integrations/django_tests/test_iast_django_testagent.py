import concurrent.futures

from mock import ANY
import pytest

from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_HASHING_TYPE
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.constants import VULN_UNTRUSTED_SERIALIZATION
from ddtrace.internal.logger import get_logger
from tests.appsec.appsec_utils import django_server
from tests.appsec.appsec_utils import gunicorn_django_server
from tests.appsec.iast.iast_utils import load_iast_report
from tests.appsec.integrations.utils_testagent import _get_span
from tests.appsec.integrations.utils_testagent import clear_session
from tests.appsec.integrations.utils_testagent import start_trace


log = get_logger(__name__)


@pytest.mark.parametrize(
    "body, content_type",
    [
        ("master", "text/plain"),
        # application/json variants
        ('"master"', "application/json"),  # raw JSON string
        ('{"key":"master"}', "application/json"),  # simple JSON object
        ('{"first":"ignore","second":"master"}', "application/json"),  # multi-key object
        ('["master","ignore"]', "application/json"),  # JSON array
        # form-encoded
        ("master_key=master", "application/x-www-form-urlencoded"),
    ],
)
@pytest.mark.parametrize("server", (gunicorn_django_server, django_server))
def test_iast_cmdi_bodies(body, content_type, server):
    """This test parametrizes body encodings to validate that IAST taints http.request.body
    across different content types and still reports CMDI on the vulnerable sink in
    tests/appsec/integrations/django_tests/django_app/views.py:command_injection
    NOTES: We use a direct call to start_trace instead of iast_test_token due to a problem with the requests.request
    wrapper witch creates many error traces, and we can't retrieve the IAST span later. this an example:
    name': 'requests.request', 'resource': 'GET /', 'meta': {'runtime-id': 'ae33e5f0844148159de930d1dd45849b',
     'component': 'requests', 'span.kind': 'client', 'http.method': 'GET', 'http.url': 'http://0.0.0.0:8000/',
      'out.host': '0.0.0.0', 'http.useragent': 'python-requests/2.32.5',
      'error.type': 'requests.exceptions.ConnectionError',
      'error.message': "HTTPConnectionPool(host='0.0.0.0', port=8000): Max retries exceeded with url: /
    """
    token = "test_iast_cmdi_bodies"
    start_trace(token)
    with server(
        use_ddtrace_cmd=False,
        iast_enabled="true",
        appsec_enabled="false",
        token=token,
        env={
            "_DD_IAST_PATCH_MODULES": (
                "benchmarks.,tests.appsec.,tests.appsec.integrations.django_tests.django_app.views."
            ),
        },
    ) as context:
        _, django_client, pid = context

        response = django_client.post(
            "/appsec/command-injection/",
            data=body,
            headers={
                "Content-Type": content_type,
            },
        )

        assert response.status_code == 200

    response_tracer = _get_span(token)
    spans_with_iast = []
    vulnerabilities = []
    clear_session(token)
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
            {"value": "dir "},
            {"redacted": True},
            {"redacted": True, "source": 0, "pattern": ANY},
        ]
    }
    assert vulnerability["location"]["spanId"]
    assert not vulnerability["location"]["path"].startswith("werkzeug")
    assert vulnerability["location"]["stackId"]
    assert vulnerability["hash"]


@pytest.mark.parametrize("server", (gunicorn_django_server, django_server))
def test_iast_untrusted_serialization_yaml(server, iast_test_token):
    with server(
        use_ddtrace_cmd=False,
        iast_enabled="true",
        token=iast_test_token,
        env={
            "_DD_IAST_PATCH_MODULES": (
                "benchmarks.,tests.appsec.,tests.appsec.integrations.django_tests.django_app.views."
            ),
        },
    ) as context:
        _, django_client, pid = context

        response = django_client.get("/iast/untrusted/yaml/?input=a: 1")

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
    assert vulnerability["type"] == VULN_UNTRUSTED_SERIALIZATION
    assert vulnerability["location"]["spanId"]
    assert vulnerability["location"]["stackId"]
    assert vulnerability["hash"]


@pytest.mark.parametrize(
    "server, config",
    (
        (
            gunicorn_django_server,
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
            gunicorn_django_server,
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
            gunicorn_django_server,
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
            gunicorn_django_server,
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
            gunicorn_django_server,
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
            gunicorn_django_server,
            {
                "workers": "1",
                "use_threads": True,
                "use_gevent": True,
                "env": {"_DD_IAST_PROPAGATION_ENABLED": "false"},
            },
        ),
        (django_server, {"env": {"DD_APM_TRACING_ENABLED": "false"}}),
    ),
)
def test_iast_vulnerable_request_downstream_django(server, config, iast_test_token):
    """Mirror Flask downstream propagation test for Django server.

    Sends a request with Datadog headers to the Django endpoint which triggers a weak-hash
    vulnerability and then calls a downstream endpoint to echo headers. Asserts that headers are
    properly propagated and that an IAST WEAK_HASH vulnerability is reported.
    """
    env = {
        "DD_APM_TRACING_ENABLED": "false",
        "DD_TRACE_URLLIB3_ENABLED": "true",
    }
    # Merge base env with parametrized env overrides
    cfg_env = dict(config.get("env", {}))
    cfg_env.update(env)
    config = dict(config)
    config["env"] = cfg_env
    with server(use_ddtrace_cmd=True, iast_enabled="true", token=iast_test_token, port=8050, **config) as context:
        _, django_client, pid = context

        trace_id = 1212121212121212121
        parent_id = 34343434
        response = django_client.get(
            "/vulnerablerequestdownstream/?port=8050",
            headers={
                "x-datadog-trace-id": str(trace_id),
                "x-datadog-parent-id": str(parent_id),
                "x-datadog-sampling-priority": "-1",
                "x-datadog-origin": "rum",
                "x-datadog-tags": "_dd.p.other=1",
            },
        )

        assert response.status_code == 200
        downstream_headers = response.json()
        assert downstream_headers["X-Datadog-Origin"] == "rum"
        assert downstream_headers["X-Datadog-Parent-Id"] != str(parent_id)
        assert downstream_headers["X-Datadog-Sampling-Priority"] == "2"

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

    assert len(spans) >= 8, f"Incorrect number of spans ({len(spans)}):\n{spans}"
    assert len(spans_with_iast) >= 2, f"Invalid number of spans with IAST ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) >= 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) >= 1
    for vulnerability in vulnerabilities[0]:
        assert vulnerability["type"] in {VULN_INSECURE_HASHING_TYPE, VULN_SSRF}
        assert vulnerability["hash"]


def test_iast_concurrent_requests_limit_django(iast_test_token):
    """Ensure only DD_IAST_MAX_CONCURRENT_REQUESTS requests have IAST enabled concurrently in Django app.

    Hits /iast-enabled concurrently; the response contains whether IAST was enabled.
    The number of True responses must equal the configured max concurrent requests.
    """
    max_concurrent = 7
    rejected_requests = 15
    total_requests = max_concurrent + rejected_requests
    delay_ms = 400

    env = {
        "DD_IAST_MAX_CONCURRENT_REQUESTS": str(max_concurrent),
    }

    with django_server(iast_enabled="true", token=iast_test_token, env=env) as context:
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
    assert true_count >= max_concurrent, f"{len(results)} requests. Expected {max_concurrent}, got {true_count}"
    assert false_count <= rejected_requests, f"{len(results)} requests. Expected {rejected_requests}, got {false_count}"


def test_iast_header_injection(iast_test_token):
    with django_server(
        iast_enabled="true",
        token=iast_test_token,
        env={
            "_DD_IAST_PATCH_MODULES": (
                "benchmarks.,tests.appsec.,tests.appsec.integrations.django_tests.django_app.views."
            ),
        },
    ) as context:
        _, django_client, pid = context

        response = django_client.post("/appsec/header-injection/", data="master\r\nInjected-Header: 1234")

        assert response.status_code == 200
        assert response.content == b"OK:master\r\nInjected-Header: 1234", f"Content is {response.content}"
        assert response.headers["Header-Injection"] == "master"
        assert response.headers["Injected-Header"] == "1234"

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
    assert vulnerability["type"] == VULN_HEADER_INJECTION
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "Header-Injection: "},
        {"value": "master\r\nInjected-Header: 1234", "source": 0},
    ]
    assert vulnerability["location"]["spanId"]
    assert vulnerability["location"]["stackId"]
    assert vulnerability["hash"]
