import concurrent.futures

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


def test_iast_untrusted_serialization_yaml():
    token = "test_iast_untrusted_serialization_yaml"
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

        response = django_client.get("/iast/untrusted/yaml/?input=a: 1")

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
def test_iast_vulnerable_request_downstream_django(server, config):
    """Mirror Flask downstream propagation test for Django server.

    Sends a request with Datadog headers to the Django endpoint which triggers a weak-hash
    vulnerability and then calls a downstream endpoint to echo headers. Asserts that headers are
    properly propagated and that an IAST WEAK_HASH vulnerability is reported.
    """
    token = "test_iast_vulnerable_request_downstream_django"
    _ = start_trace(token)
    env = {
        "DD_APM_TRACING_ENABLED": "false",
        "DD_TRACE_URLLIB3_ENABLED": "true",
    }
    # Merge base env with parametrized env overrides
    cfg_env = dict(config.get("env", {}))
    cfg_env.update(env)
    config = dict(config)
    config["env"] = cfg_env
    # TODO(APPSEC-59081): without use_ddtrace_cmd=False it raises a `NameError: name 'sys' is not defined`
    #   File "/proj/dd-trace-py/ddtrace/internal/module.py", line 301, in _exec_module
    #     self.loader.exec_module(module)
    #   File "<frozen importlib._bootstrap_external>", line 883, in exec_module
    #   File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed
    #   File "/proj/dd-trace-py/tests/appsec/integrations/django_tests/django_app/wsgi.py", line 13, in <module>
    #     application = get_wsgi_application()
    #   File "/proj/venv310/site-packages/django/core/wsgi.py", line 12, in get_wsgi_application
    #     django.setup(set_prefix=False)
    #   File "/proj/venv310/site-packages/django/__init__.py", line 24, in setup
    #     apps.populate(settings.INSTALLED_APPS)
    #   File "/proj/dd-trace-py/ddtrace/contrib/internal/trace_utils.py", line 315, in wrapper
    #     return func(mod, pin, wrapped, instance, args, kwargs)
    #   File "/proj/dd-trace-py/ddtrace/contrib/internal/django/patch.py", line 124, in traced_populate
    #     ret = func(*args, **kwargs)
    #   File "/proj/venv310/site-packages/django/apps/registry.py", line 91, in populate
    #     app_config = AppConfig.create(entry)
    #   File "/proj/venv310/site-packages/django/apps/config.py", line 121, in create
    #     if module_has_submodule(app_module, APPS_MODULE_NAME):
    #   File "/proj/venv310/site-packages/django/utils/module_loading.py", line 85, in module_has_submodule
    #     return importlib_find(full_module_name, package_path) is not None
    #   File "/python310importlib/util.py", line 103, in find_spec
    #     return _find_spec(fullname, parent_path)
    #   File "/python310importlib/_bootstrap.py", line 923, in _find_spec
    #     meta_path = sys.meta_path
    #  NameError: name 'sys' is not defined
    with server(iast_enabled="true", appsec_enabled="false", token=token, port=8050, **config) as context:
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

    assert len(spans) >= 8, f"Incorrect number of spans ({len(spans)}):\n{spans}"
    assert len(spans_with_iast) >= 2, f"Invalid number of spans with IAST ({len(spans_with_iast)}):\n{spans_with_iast}"
    assert len(vulnerabilities) >= 1, f"Invalid number of vulnerabilities ({len(vulnerabilities)}):\n{vulnerabilities}"
    assert len(vulnerabilities[0]) >= 1
    for vulnerability in vulnerabilities[0]:
        assert vulnerability["type"] in {VULN_INSECURE_HASHING_TYPE, VULN_SSRF}
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
    assert true_count >= max_concurrent, f"{len(results)} requests. Expected {max_concurrent}, got {true_count}"
    assert false_count <= rejected_requests, f"{len(results)} requests. Expected {rejected_requests}, got {false_count}"


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
