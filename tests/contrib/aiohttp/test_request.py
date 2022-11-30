import asyncio
import aiohttp
import threading
from urllib import request

from ddtrace import config
from ddtrace import config
from ddtrace.contrib.aiohttp.middlewares import trace_app
from ddtrace.contrib.aiohttp.patch import patch
from ddtrace.contrib.aiohttp.patch import unpatch
from tests.utils import assert_is_measured
from tests.utils import TestSpan
from ..asyncio.utils import mark_asyncio_no_close as mark_asyncio
from tests.utils import override_global_config
from .utils import TraceTestCase
from .app.web import setup_app


# TODO: move to new client test file
class TestRequestTracing(TraceTestCase):
    """
    Ensures that the trace includes all traced components.
    """

    def enable_tracing(self):
        # enabled tracing:
        #   * middleware
        #   * templates
        trace_app(self.app, self.tracer, distributed_tracing=True)
        patch(enable_distributed=True)

        Pin.override(aiohttp.ClientSession, tracer=self.tracer)
        Pin.override(aiohttp_jinja2, tracer=self.tracer)

        config.aiohttp_client.trace_query_string = True
        config.aiohttp_client.redact_query_keys.add('baz')

    def disable_tracing(self):
        unpatch()
        config.aiohttp_client.trace_query_string = False
        config.aiohttp_client.redact_query_keys.remove('baz')

    @mark_asyncio
    async def test_aiohttp_client_tracer(self):
        async with aiohttp.ClientSession() as session:
            url = self.client.make_url('/?foo=bar&baz=bif')
            trace_url = url.with_query(foo='bar', baz='--redacted--')
            async with session.get(url) as response:
                await response.read()
        traces = self.tracer.writer.pop_traces()
        assert len(traces) == 3

        # client request span
        assert len(traces[1]) == 4
        client_request_span = traces[1][0]
        root_span_id = client_request_span.span_id
        root_trace_id = client_request_span.trace_id

        TestSpan(client_request_span).assert_matches(
            name="ClientSession.request",
            service="aiohttp.client",
            parent_id=None,
            resource="/",
            meta={'http.url': str(url.with_query(dict())), 'http.method': 'GET', 'http.status_code': str(200),
                  'http.query.string': trace_url.query_string}
        )
        # TCPConnector.connect
        connector_connect_span = traces[1][1]
        TestSpan(connector_connect_span).assert_matches(
            name="TCPConnector.connect",
            parent_id=root_span_id,
            trace_id=root_trace_id,
            service="aiohttp.client",
            resource="/",
        )

        # TCPConnector._create_connection
        connector_create_connection_span = traces[1][2]
        TestSpan(connector_create_connection_span).assert_matches(
            name="TCPConnector._create_connection",
            parent_id=connector_connect_span.span_id,
            trace_id=connector_connect_span.trace_id,
            service="aiohttp.client",
            resource="/",
        )

        # client start span
        client_start_span = traces[1][3]
        TestSpan(client_start_span).assert_matches(
            name="ClientResponse.start",
            parent_id=root_span_id,
            trace_id=root_trace_id,
            service="aiohttp.client",
            resource="/",
        )

        # web server request span
        assert len(traces[0]) == 1
        server_request_span = traces[0][0]
        TestSpan(server_request_span).assert_matches(
            name="aiohttp.request",
            service="aiohttp-web",
            resource="GET /",
            parent_id=root_span_id,
            trace_id=root_trace_id,
        )

        # client read span
        assert len(traces[2]) == 1
        read_span = traces[2][0]
        TestSpan(read_span).assert_matches(
            name="StreamReader.read",
            service="aiohttp.client",
            resource="/",
            parent_id=root_span_id,
            trace_id=root_trace_id,
        )


async def test_full_request(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
        await request.text()
    # the trace is created
    traces = tracer.pop_traces()
    assert 3 == len(traces)
    assert 1 == len(traces[0])

        # request
    request_span = traces[0][0]
    assert_is_measured(request_span)
        TestSpan(request_span).assert_matches(
            name="aiohttp.request",
            service="aiohttp-web",
            resource="GET /template/",
            meta={'http.url': str(trace_url), 'http.method': 'GET', 'http.status_code': str(200)}
        )

    # request
    assert "aiohttp-web" == request_span.service
    assert "aiohttp.request" == request_span.name
    assert "GET /" == request_span.resource


async def test_multiple_full_request(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    # it should handle multiple requests using the same loop
    def make_requests():
        url = client.make_url("/delayed/")
        response = request.urlopen(str(url)).read().decode("utf-8")
        assert "Done" == response

    # blocking call executed in different threads
    threads = [threading.Thread(target=make_requests) for _ in range(10)]
    for t in threads:
        t.daemon = True
        t.start()

    # we should yield so that this loop can handle
    # threads' requests
        await asyncio.sleep(0.5)
    for t in threads:
        t.join(timeout=0.5)

    # the trace is created
    traces = tracer.pop_traces()
    assert 10 == len(traces)
    assert 1 == len(traces[0])


async def _test_user_specified_service(tracer, aiohttp_client, loop):
    """
    When a service name is specified by the user
        The aiohttp integration should use it as the service name
    """
    unpatch()
    with override_global_config(dict(service="mysvc")):
        patch()
        app = setup_app()
        trace_app(app, tracer)
        client = await aiohttp_client(app)
        request = await client.request("GET", "/")
        await request.text()
        traces = tracer.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert request_span.service == "mysvc"


async def test_http_request_header_tracing(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    config.aiohttp.http.trace_headers(["my-header"])
    request = await client.request("GET", "/", headers={"my-header": "my_value"})
    await request.text()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.request.headers.my-header") == "my_value"


async def test_http_response_header_tracing(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    config.aiohttp.http.trace_headers(["my-response-header"])
    request = await client.request("GET", "/response_headers/")
    await request.text()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])

    request_span = traces[0][0]
    assert request_span.service == "aiohttp-web"
    assert request_span.get_tag("http.response.headers.my-response-header") == "my_response_value"

        # client spans
        assert len(traces[1]) == 4  # these are tested via client tests

    @TraceTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        self.loop.run_until_complete(self._test_user_specified_service())
