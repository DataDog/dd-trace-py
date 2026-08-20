import httpx2
import pytest

from ddtrace import config
from ddtrace.contrib.internal.httpx2.patch import patch
from ddtrace.contrib.internal.httpx2.patch import unpatch
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.settings.http import HttpConfig
from tests.utils import override_config
from tests.utils import override_http_config


# host:port of httpbin container
HOST = "localhost"
PORT = 8001

DEFAULT_HEADERS = {
    "User-Agent": "python-httpx2/x.xx.x",
}


def get_url(path):
    # type: (str) -> str
    return "http://{}:{}{}".format(HOST, PORT, path)


@pytest.fixture(autouse=True)
def patch_httpx2():
    patch()
    try:
        yield
    finally:
        unpatch()


def test_patching():
    """
    When patching httpx2 library
        We wrap the correct methods
    When unpatching httpx2 library
        We unwrap the correct methods
    """
    assert is_wrapted(httpx2.Client.send)
    assert is_wrapted(httpx2.AsyncClient.send)
    assert is_wrapted(httpx2.Client._send_single_request)
    assert is_wrapted(httpx2.AsyncClient._send_single_request)

    unpatch()
    assert not is_wrapted(httpx2.Client.send)
    assert not is_wrapted(httpx2.AsyncClient.send)
    assert not is_wrapted(httpx2.Client._send_single_request)
    assert not is_wrapted(httpx2.AsyncClient._send_single_request)


@pytest.mark.skipif(not hasattr(httpx2, "alias_httpx"), reason="httpx2.alias_httpx requires httpx2>=2.9.0")
@pytest.mark.subprocess()
@pytest.mark.snapshot()
def test_alias_httpx():
    import httpx2

    httpx2.alias_httpx()

    import httpx

    from ddtrace import patch

    patch(httpx2=True, httpx=True)

    response = httpx.get("http://localhost:8001/status/200")
    assert response.status_code == 200


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_httpx2_service_name():
    """
    When using split_by_domain
        We set the span service name as a text type and not binary
    """
    client = httpx2.Client()

    with override_config("httpx2", {"split_by_domain": True}):
        resp = client.get(get_url("/status/200"))
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_get_200():
    url = get_url("/status/200")

    resp = httpx2.get(url, headers=DEFAULT_HEADERS)
    assert resp.status_code == 200

    async with httpx2.AsyncClient() as client:
        resp = await client.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_configure_service_name():
    """
    When setting ddtrace.config.httpx2.service_name directly
        We use the value from ddtrace.config.httpx2.service_name
    """
    url = get_url("/status/200")

    with override_config("httpx2", {"service_name": "test-httpx2-service-name"}):
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200

        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.subprocess(
    env=dict(
        DD_HTTPX2_SERVICE="env-overridden-service-name",
        DD_SERVICE="global-service-name",
    )
)
@pytest.mark.snapshot()
def test_configure_service_name_env():
    """
    When setting DD_HTTPX2_SERVICE env variable
        When DD_SERVICE is also set
            We use the value from DD_HTTPX2_SERVICE
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name"))
@pytest.mark.snapshot()
def test_schematized_configure_global_service_name_env_default():
    """
    v0/default: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
@pytest.mark.snapshot()
def test_schematized_configure_global_service_name_env_v0():
    """
    v0/default: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
@pytest.mark.snapshot()
def test_schematized_configure_global_service_name_env_v1():
    """
    v1: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess()
@pytest.mark.snapshot()
def test_schematized_unspecified_service_name_env_default():
    """
    v0/default: With no service name, we use httpx2
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
@pytest.mark.snapshot()
def test_schematized_unspecified_service_name_env_v0():
    """
    v0/default: With no service name, we use httpx2
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
@pytest.mark.snapshot()
def test_schematized_unspecified_service_name_env_v1():
    """
    v1: With no service name, we expect ddtrace.internal.DEFAULT_SPAN_SERVICE_NAME
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
@pytest.mark.snapshot()
def test_schematized_operation_name_env_v0():
    """
    v0: Operation name is http.request
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
@pytest.mark.snapshot()
def test_schematized_operation_name_env_v1():
    """
    v1: Operation name is http.client.request
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/status/200")
    httpx2.get(url, headers=DEFAULT_HEADERS)

    async def test():
        async with httpx2.AsyncClient() as client:
            await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_get_500():
    """
    When the status code is 500
        We mark the span as an error
    """
    url = get_url("/status/500")
    resp = httpx2.get(url, headers=DEFAULT_HEADERS)
    assert resp.status_code == 500

    async with httpx2.AsyncClient() as client:
        resp = await client.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 500


@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.http.useragent"])
def test_connection_error():
    with pytest.raises(httpx2.ConnectError):
        httpx2.get("http://127.0.0.1:1")


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_split_by_domain():
    """
    When split_by_domain is configured
        We set the service name to the <host>:<port>
    """
    url = get_url("/status/200")

    with override_config("httpx2", {"split_by_domain": True}):
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200

        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_trace_query_string():
    """
    When trace_query_string is enabled
        We include the query string as a tag on the span
    """
    url = get_url("/status/200?some=query&string=args")
    with override_http_config("httpx2", {"trace_query_string": True}):
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200

        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_request_headers():
    """
    When request headers are configured for this integration
        We add the request headers as tags on the span
    """
    url = get_url("/response-headers?Some-Response-Header=Response-Value")

    headers = {
        "Some-Request-Header": "Request-Value",
        "User-Agent": "python-httpx2/x.xx.x",
    }

    try:
        config.httpx2.http.trace_headers(["Some-Request-Header", "Some-Response-Header"])
        resp = httpx2.get(url, headers=headers)
        assert resp.status_code == 200

        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            assert resp.status_code == 200
    finally:
        config.httpx2.http = HttpConfig()


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

    resp = httpx2.get(url, headers=DEFAULT_HEADERS)
    assert_request_headers(resp)

    async with httpx2.AsyncClient() as client:
        resp = await client.get(url, headers=DEFAULT_HEADERS)
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

    with override_config("httpx2", {"distributed_tracing": False}):
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert_request_headers(resp)

        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert_request_headers(resp)


@pytest.mark.subprocess(env=dict(DD_HTTPX2_DISTRIBUTED_TRACING="false"))
def test_distributed_tracing_disabled_env():
    """
    When disabling distributed tracing via env variable
        We do not add distributed tracing headers to outbound requests
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import DEFAULT_HEADERS
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/headers")

    def assert_request_headers(response):
        data = response.json()
        assert "X-Datadog-Trace-Id" not in data["headers"]
        assert "X-Datadog-Parent-Id" not in data["headers"]
        assert "X-Datadog-Sampling-Priority" not in data["headers"]

    resp = httpx2.get(url, headers=DEFAULT_HEADERS)
    assert_request_headers(resp)

    async def test():
        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert_request_headers(resp)

    asyncio.run(test())
