"""Tests for httpx2 integration with dd-trace-py."""

from typing import Any
from typing import Callable

import httpx2
import pytest

from ddtrace import config
from ddtrace.contrib.internal.httpx2.patch import patch
from ddtrace.contrib.internal.httpx2.patch import unpatch
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.settings.http import HttpConfig
from tests.utils import override_config
from tests.utils import override_http_config


# Host and port of httpbin container
HOST = "localhost"
PORT = 8001

DEFAULT_HEADERS = {
    "User-Agent": "python-httpx2/x.xx.x",
}


def get_url(path: str) -> str:
    """Construct a test URL for the given path.

    Args:
        path: URL path to append to the base URL

    Returns:
        Full URL for testing
    """
    return "http://{}:{}{}".format(HOST, PORT, path)


@pytest.fixture(autouse=True)
def patch_httpx2() -> Any:
    """Automatically patch httpx2 for all tests.

    Yields:
        None after patching, cleans up after test
    """
    patch()
    try:
        yield
    finally:
        unpatch()


def test_patching() -> None:
    """Test that patching and unpatching httpx2 works correctly.

    When patching httpx2 library
        We wrap the correct methods on Client and AsyncClient
    When unpatching httpx2 library
        We unwrap the correct methods
    """
    assert is_wrapted(httpx2.Client.send)
    assert is_wrapted(httpx2.AsyncClient.send)

    unpatch()
    assert not is_wrapted(httpx2.Client.send)
    assert not is_wrapted(httpx2.AsyncClient.send)


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_httpx2_service_name() -> None:
    """Test service name configuration with split_by_domain.

    When using split_by_domain
        We set the span service name correctly as a text type
        The service name should be the host:port of the target
    """
    client = httpx2.Client()

    with override_config("httpx2", {"split_by_domain": True}):
        resp = client.get(get_url("/status/200"))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_200(snapshot_context: Callable[..., Any]) -> None:
    """Test basic GET request returning 200 status.

    When making a successful GET request
        We create a span with correct tags
        The span should include http.method, http.url, http.status_code
        Both sync and async clients should produce equivalent spans
    """
    url = get_url("/status/200")

    with snapshot_context():
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200

    with snapshot_context():
        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_configure_service_name(snapshot_context: Callable[..., Any]) -> None:
    """Test configuring service name via ddtrace.config.

    When setting ddtrace.config.httpx2.service_name directly
        We use the value from ddtrace.config.httpx2.service_name
        Both sync and async requests should use the configured service name
    """
    url = get_url("/status/200")

    with override_config("httpx2", {"service_name": "test-httpx2-service-name"}):
        with snapshot_context():
            resp = httpx2.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200

        with snapshot_context():
            async with httpx2.AsyncClient() as client:
                resp = await client.get(url, headers=DEFAULT_HEADERS)
                assert resp.status_code == 200


@pytest.mark.subprocess(
    env=dict(
        DD_HTTPX2_SERVICE="env-overridden-service-name",
        DD_SERVICE="global-service-name",
    )
)
def test_configure_service_name_env() -> None:
    """Test service name configuration via environment variables.

    When setting DD_HTTPX2_SERVICE env variable
        When DD_SERVICE is also set
            We use the value from DD_HTTPX2_SERVICE
            The integration-specific env var takes precedence
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_configure_service_name_env"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                DEFAULT_HEADERS = {
                    "User-Agent": "python-httpx2/x.xx.x",
                }
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name"))
def test_schematized_configure_global_service_name_env_default() -> None:
    """Test v0/default schema with DD_SERVICE set.

    v0/default: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_configure_global_service_name_env_default"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_schematized_configure_global_service_name_env_v0() -> None:
    """Test v0 schema with DD_SERVICE set.

    v0: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_configure_global_service_name_env_v0"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_SERVICE="global-service-name", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
def test_schematized_configure_global_service_name_env_v1() -> None:
    """Test v1 schema with DD_SERVICE set.

    v1: When only setting DD_SERVICE
        We use the value from DD_SERVICE for the service name
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_configure_global_service_name_env_v1"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess()
def test_schematized_unspecified_service_name_env_default() -> None:
    """Test v0/default schema with no service name configuration.

    v0/default: With no service name, we use httpx2
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_unspecified_service_name_env_default"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_schematized_unspecified_service_name_env_v0() -> None:
    """Test v0 schema with no service name configuration.

    v0: With no service name, we use httpx2
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_unspecified_service_name_env_v0"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
def test_schematized_unspecified_service_name_env_v1() -> None:
    """Test v1 schema with no service name configuration.

    v1: With no service name, we expect ddtrace.internal.DEFAULT_SPAN_SERVICE_NAME
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_unspecified_service_name_env_v1"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_schematized_operation_name_env_v0() -> None:
    """Test v0 schema operation name.

    v0: Operation name is httpx2.request
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_operation_name_env_v0"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
def test_schematized_operation_name_env_v1() -> None:
    """Test v1 schema operation name.

    v1: Operation name is http.client.request
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url
    from tests.utils import snapshot_context

    patch()
    url = get_url("/status/200")

    async def test() -> None:
        token = "tests.contrib.httpx2.test_httpx2.test_schematized_operation_name_env_v1"
        with snapshot_context(wait_for_num_traces=1, token=token):
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            httpx2.get(url, headers=DEFAULT_HEADERS)

        with snapshot_context(wait_for_num_traces=1, token=token):
            async with httpx2.AsyncClient() as client:
                await client.get(url, headers=DEFAULT_HEADERS)

    asyncio.run(test())


@pytest.mark.asyncio
async def test_get_500(snapshot_context: Callable[..., Any]) -> None:
    """Test GET request with 500 server error.

    When the status code is 500
        We mark the span as an error
        The span should include error=True and error tags
    """
    url = get_url("/status/500")
    with snapshot_context(wait_for_num_traces=1):
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 500

    with snapshot_context(wait_for_num_traces=1):
        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 500


@pytest.mark.asyncio
async def test_split_by_domain(snapshot_context: Callable[..., Any]) -> None:
    """Test split_by_domain configuration.

    When split_by_domain is configured
        We set the service name to the <host>:<port>
    """
    url = get_url("/status/200")

    with override_config("httpx2", {"split_by_domain": True}):
        with snapshot_context(wait_for_num_traces=1):
            resp = httpx2.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200

        with snapshot_context(wait_for_num_traces=1):
            async with httpx2.AsyncClient() as client:
                resp = await client.get(url, headers=DEFAULT_HEADERS)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_trace_query_string(snapshot_context: Callable[..., Any]) -> None:
    """Test trace_query_string configuration.

    When trace_query_string is enabled
        We include the query string as a tag on the span
    """
    url = get_url("/status/200?some=query&string=args")
    headers = {
        "User-Agent": "python-httpx2/x.xx.x",
    }
    with override_http_config("httpx2", {"trace_query_string": True}):
        with snapshot_context(wait_for_num_traces=1):
            resp = httpx2.get(url, headers=headers)
            assert resp.status_code == 200

        with snapshot_context(wait_for_num_traces=1):
            async with httpx2.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_headers(snapshot_context: Callable[..., Any]) -> None:
    """Test capturing request and response headers.

    When request headers are configured for this integration
        We add the request headers as tags on the span
        We also capture configured response headers
    """
    url = get_url("/response-headers?Some-Response-Header=Response-Value")

    headers = {
        "Some-Request-Header": "Request-Value",
        "User-Agent": "python-httpx2/x.xx.x",
    }

    try:
        config.httpx2.http.trace_headers(["Some-Request-Header", "Some-Response-Header"])
        with snapshot_context(wait_for_num_traces=1):
            resp = httpx2.get(url, headers=headers)
            assert resp.status_code == 200

        with snapshot_context(wait_for_num_traces=1):
            async with httpx2.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                assert resp.status_code == 200
    finally:
        config.httpx2.http = HttpConfig()


@pytest.mark.asyncio
async def test_distributed_tracing_headers() -> None:
    """Test distributed tracing header injection.

    By default
        Distributed tracing headers are added to outbound requests
        X-Datadog-Trace-Id, X-Datadog-Parent-Id, X-Datadog-Sampling-Priority
    """
    url = get_url("/headers")

    def assert_request_headers(response: httpx2.Response) -> None:
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
async def test_distributed_tracing_disabled() -> None:
    """Test disabling distributed tracing.

    When distributed_tracing is disabled
        We do not add distributed tracing headers to outbound requests
    """
    url = get_url("/headers")

    def assert_request_headers(response: httpx2.Response) -> None:
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
def test_distributed_tracing_disabled_env() -> None:
    """Test disabling distributed tracing via environment variable.

    When disabling distributed tracing via env variable
        We do not add distributed tracing headers to outbound requests
    """
    import asyncio

    import httpx2

    from ddtrace.contrib.internal.httpx2.patch import patch
    from tests.contrib.httpx2.test_httpx2 import get_url

    patch()
    url = get_url("/headers")

    async def test() -> None:
        def assert_request_headers(response: httpx2.Response) -> None:
            data = response.json()
            assert "X-Datadog-Trace-Id" not in data["headers"]
            assert "X-Datadog-Parent-Id" not in data["headers"]
            assert "X-Datadog-Sampling-Priority" not in data["headers"]

        DEFAULT_HEADERS = {
            "User-Agent": "python-httpx2/x.xx.x",
        }
        resp = httpx2.get(url, headers=DEFAULT_HEADERS)
        assert_request_headers(resp)

        async with httpx2.AsyncClient() as client:
            DEFAULT_HEADERS = {
                "User-Agent": "python-httpx2/x.xx.x",
            }
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert_request_headers(resp)

    asyncio.run(test())


@pytest.mark.asyncio
async def test_post_request(snapshot_context: Callable[..., Any]) -> None:
    """Test POST request with JSON body.

    When making a POST request with JSON data
        We create a span with correct method and status
        The span should capture POST as the http.method
    """
    url = get_url("/post")

    with snapshot_context():
        resp = httpx2.post(url, json={"key": "value"}, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200

    with snapshot_context():
        async with httpx2.AsyncClient() as client:
            resp = await client.post(url, json={"key": "value"}, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_client_send(snapshot_context: Callable[..., Any]) -> None:
    """Test Client.send() method directly.

    When using Client.send() with a Request object
        We create appropriate spans
        Both sync and async clients should work correctly
    """
    url = get_url("/status/200")

    with snapshot_context():
        client = httpx2.Client()
        request = httpx2.Request("GET", url, headers=DEFAULT_HEADERS)
        resp = client.send(request)
        assert resp.status_code == 200

    with snapshot_context():
        async with httpx2.AsyncClient() as client:
            request = httpx2.Request("GET", url, headers=DEFAULT_HEADERS)
            resp = await client.send(request)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_redirects(snapshot_context: Callable[..., Any]) -> None:
    """Test redirect handling.

    When following redirects
        We should create spans for each request in the redirect chain
        The final response status should be 200
    """
    url = get_url("/redirect/2")

    with snapshot_context():
        resp = httpx2.get(url, headers=DEFAULT_HEADERS, follow_redirects=True)
        assert resp.status_code == 200

    with snapshot_context():
        async with httpx2.AsyncClient() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_timeout_error(snapshot_context: Callable[..., Any]) -> None:
    """Test handling of timeout errors.

    When a request times out
        We should mark the span as an error
        Error tags should be set appropriately
    """
    url = get_url("/delay/10")

    with snapshot_context():
        with pytest.raises(httpx2.TimeoutException):
            httpx2.get(url, headers=DEFAULT_HEADERS, timeout=0.1)

    with snapshot_context():
        async with httpx2.AsyncClient() as client:
            with pytest.raises(httpx2.TimeoutException):
                await client.get(url, headers=DEFAULT_HEADERS, timeout=0.1)


@pytest.mark.asyncio
async def test_connection_error(snapshot_context: Callable[..., Any]) -> None:
    """Test handling of connection errors.

    When a connection fails
        We should mark the span as an error
        Error tags should include the exception details
    """
    url = "http://localhost:1"  # Invalid port

    with snapshot_context():
        with pytest.raises((httpx2.ConnectError, httpx2.ConnectTimeout)):
            httpx2.get(url, headers=DEFAULT_HEADERS, timeout=1)

    with snapshot_context():
        async with httpx2.AsyncClient() as client:
            with pytest.raises((httpx2.ConnectError, httpx2.ConnectTimeout)):
                await client.get(url, headers=DEFAULT_HEADERS, timeout=1)
