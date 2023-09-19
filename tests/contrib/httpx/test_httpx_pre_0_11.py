import httpx
import pytest
import six
from wrapt import ObjectProxy

from ddtrace import config
from ddtrace.contrib.httpx.patch import HTTPX_VERSION
from ddtrace.contrib.httpx.patch import patch
from ddtrace.contrib.httpx.patch import unpatch
from ddtrace.pin import Pin
from ddtrace.settings.http import HttpConfig
from tests.utils import override_config
from tests.utils import override_http_config


# host:port of httpbin_local container
HOST = "localhost"
PORT = 8001

DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}


pytestmark = pytest.mark.skipif(HTTPX_VERSION >= (0, 11), reason="httpx<=0.10 Client is asynchronous")


def get_url(path):
    # type: (str) -> str
    return "http://{}:{}{}".format(HOST, PORT, path)


@pytest.fixture(autouse=True)
def patch_httpx():
    patch()
    try:
        yield
    finally:
        unpatch()


def test_patching():
    """
    When patching httpx library
        We wrap the correct methods
    When unpatching httpx library
        We unwrap the correct methods
    """
    assert isinstance(httpx.Client.send, ObjectProxy)
    unpatch()
    assert not isinstance(httpx.Client.send, ObjectProxy)


@pytest.mark.asyncio
async def test_httpx_service_name(tracer, test_spans):
    """
    When using split_by_domain
        We set the span service name as a text type and not binary
    """
    client = httpx.Client()
    Pin.override(client, tracer=tracer)

    with override_config("httpx", {"split_by_domain": True}):
        resp = await client.get(get_url("/status/200"))
    assert resp.status_code == 200

    traces = test_spans.pop_traces()
    assert len(traces) == 1

    spans = traces[0]
    assert len(spans) == 1
    assert isinstance(spans[0].service, six.text_type)


@pytest.mark.asyncio
async def test_get_200(snapshot_context):
    token = "tests.contrib.httpx.test_httpx.test_get_200"
    url = get_url("/status/200")

    with snapshot_context(token=token):
        resp = await httpx.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_configure_service_name(snapshot_context):
    """
    When setting ddtrace.config.httpx.service_name directly
        We use the value from ddtrace.config.httpx.service_name
    """
    token = "tests.contrib.httpx.test_httpx.test_configure_service_name"
    url = get_url("/status/200")

    with override_config("httpx", {"service_name": "test-httpx-service-name"}):
        with snapshot_context(token=token):
            resp = await httpx.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_configure_service_name_pin(tracer, test_spans):
    """
    When setting service name via a Pin
        We use the value from the Pin
    """
    url = get_url("/status/200")

    def assert_spans(test_spans, service):
        test_spans.assert_trace_count(1)
        test_spans.assert_span_count(1)
        assert test_spans.spans[0].service == service
        test_spans.reset()

    # override the tracer on the default sync client
    # DEV: `httpx.get` will call `with Client() as client: client.get()`
    Pin.override(httpx.Client, tracer=tracer)

    # sync client
    client = httpx.Client()
    Pin.override(client, service="sync-client", tracer=tracer)

    resp = await httpx.get(url, headers=DEFAULT_HEADERS)
    assert resp.status_code == 200
    assert_spans(test_spans, service=None)

    resp = await client.get(url, headers=DEFAULT_HEADERS)
    assert resp.status_code == 200
    assert_spans(test_spans, service="sync-client")


@pytest.mark.subprocess(
    env=dict(
        DD_HTTPX_SERVICE="env-overridden-service-name",
        DD_SERVICE="global-service-name",
    )
)
def test_configure_service_name_env():
    """
    When setting DD_HTTPX_SERVICE env variable
        When DD_SERVICE is also set
            We use the value from DD_HTTPX_SERVICE
    """
    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_configure_service_name_env"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name"))
def test_configure_global_service_name_env():
    """
    When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_configure_global_service_name_env"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name"))
def test_schematized_configure_global_service_name_env_default():
    """
    v0/default: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_configure_global_service_name_env_default"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_schematized_configure_global_service_name_env_v0():
    """
    v0/default: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_configure_global_service_name_env_v0"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
def test_schematized_configure_global_service_name_env_v1():
    """
    v1: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_configure_global_service_name_env_v1"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess()
def test_schematized_unspecified_service_name_env_default():
    """
    v0/default: With no service name, we use httpx
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_unspecified_service_name_env_default"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_schematized_unspecified_service_name_env_v0():
    """
    v0/default: With no service name, we use httpx
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_unspecified_service_name_env_v0"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
def test_schematized_unspecified_service_name_env_v1():
    """
    v1: With no service name, we expect ddtrace.internal.DEFAULT_SPAN_SERVICE_NAME
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_unspecified_service_name_env_v1"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_schematized_operation_name_env_v0():
    """
    v0: Operation name is httpx.request
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_operation_name_env_v0"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
def test_schematized_operation_name_env_v1():
    """
    v0: Operation name is http.client.request
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test():
        token = "tests.contrib.httpx.test_httpx.test_schematized_operation_name_env_v1"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx/x.xx.x",
            }
            await httpx.get(url, headers=DEFAULT_HEADERS)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())


@pytest.mark.asyncio
async def test_get_500(snapshot_context):
    """
    When the status code is 500
        We mark the span as an error
    """
    token = "tests.contrib.httpx.test_httpx.test_get_500"
    url = get_url("/status/500")
    with snapshot_context(wait_for_num_traces=1, token=token):
        resp = await httpx.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_split_by_domain(snapshot_context):
    """
    When split_by_domain is configured
        We set the service name to the <host>:<port>
    """
    token = "tests.contrib.httpx.test_httpx.test_split_by_domain"
    url = get_url("/status/200")

    with override_config("httpx", {"split_by_domain": True}):
        with snapshot_context(wait_for_num_traces=1, token=token):
            resp = await httpx.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_query_string(snapshot_context):
    """
    When trace_query_string is enabled
        We include the query string as a tag on the span
    """
    token = "tests.contrib.httpx.test_httpx.test_trace_query_string"
    url = get_url("/status/200?some=query&string=args")
    # Caveat(avara1986): Docker image "httpbin" set as user-agent "python-httpx/0.23.0".
    # your local container or the CI container, could have different version. We set
    # the user agent to use the same version.
    headers = {
        "User-Agent": "python-httpx/x.xx.x",
    }
    with override_http_config("httpx", {"trace_query_string": True}):
        with snapshot_context(wait_for_num_traces=1, token=token):
            resp = await httpx.get(url, headers=headers)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_headers(snapshot_context):
    """
    When request headers are configured for this integration
        We add the request headers as tags on the span
    """
    token = "tests.contrib.httpx.test_httpx.test_request_headers"
    url = get_url("/response-headers?Some-Response-Header=Response-Value")

    headers = {
        "Some-Request-Header": "Request-Value",
        "User-Agent": "python-httpx/x.xx.x",
    }

    try:
        config.httpx.http.trace_headers(["Some-Request-Header", "Some-Response-Header"])
        with snapshot_context(wait_for_num_traces=1, token=token):
            resp = await httpx.get(url, headers=headers)
            assert resp.status_code == 200
    finally:
        config.httpx.http = HttpConfig()


@pytest.mark.asyncio
async def test_distributed_tracing_headers():
    """
    By default
        Distributed tracing headers are added to outbound requests
    """
    url = get_url("/headers")

    def assert_request_headers(response):
        data = response.json()
        assert "X-Datadog-Trace-Id" in data["headers"]
        assert "X-Datadog-Parent-Id" in data["headers"]
        assert "X-Datadog-Sampling-Priority" in data["headers"]

    resp = await httpx.get(url, headers=DEFAULT_HEADERS)
    assert_request_headers(resp)


@pytest.mark.asyncio
async def test_distributed_tracing_disabled():
    """
    When distributed_tracing is disabled
        We do not add distributed tracing headers to outbound requests
    """
    url = get_url("/headers")

    def assert_request_headers(response):
        data = response.json()
        assert "X-Datadog-Trace-Id" not in data["headers"]
        assert "X-Datadog-Parent-Id" not in data["headers"]
        assert "X-Datadog-Sampling-Priority" not in data["headers"]

    with override_config("httpx", {"distributed_tracing": False}):
        resp = await httpx.get(url, headers=DEFAULT_HEADERS)
        assert_request_headers(resp)


@pytest.mark.subprocess(env=dict(DD_HTTPX_DISTRIBUTED_TRACING="false"))
def test_distributed_tracing_disabled_env():
    """
    When disabling distributed tracing via env variable
        We do not add distributed tracing headers to outbound requests
    """

    import asyncio
    import sys

    import httpx

    from ddtrace.contrib.httpx import patch
    from tests.contrib.httpx.test_httpx import get_url

    patch()
    url = get_url("/headers")

    async def test():
        def assert_request_headers(response):
            data = response.json()
            assert "X-Datadog-Trace-Id" not in data["headers"]
            assert "X-Datadog-Parent-Id" not in data["headers"]
            assert "X-Datadog-Sampling-Priority" not in data["headers"]

        DEFAULT_HEADERS = {
            "User-Agent": "python-httpx/x.xx.x",
        }
        resp = await httpx.get(url, headers=DEFAULT_HEADERS)
        assert_request_headers(resp)

    if sys.version_info >= (3, 7, 0):
        asyncio.run(test())
    else:
        asyncio.get_event_loop().run_until_complete(test())
