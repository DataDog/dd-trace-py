import threading
import asyncio
import aiohttp
import aiohttp_jinja2

from urllib import request
from aiohttp.test_utils import unittest_run_loop

from ddtrace.pin import Pin
from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ddtrace.contrib.aiohttp.middlewares import trace_app

from .utils import TraceTestCase
from ...utils.span import TestSpan
from ...utils import assert_is_measured


class TestRequestTracing(TraceTestCase):
    def setUp(self):
        super().setUp()
        asyncio.set_event_loop(self.loop)

    """
    Ensures that the trace includes all traced components.
    """

    def enable_tracing(self):
        # enabled tracing:
        #   * middleware
        #   * templates
        trace_app(self.app, self.tracer, distributed_tracing=True)
        patch(self.tracer, enable_distributed=True)
        Pin.override(aiohttp_jinja2, tracer=self.tracer)

    def disable_tracing(self):
        unpatch()

    @unittest_run_loop
    @asyncio.coroutine
    def test_aiohttp_client_tracer(self):
        session = aiohttp.ClientSession()
        url = self.client.make_url('/')
        result = yield from session.get(url)
        yield from result.read()
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
            name="ClientResponse.read",
            service="aiohttp.client",
            resource="/",
            parent_id=root_span_id,
            trace_id=root_trace_id,
        )

    @unittest_run_loop
    @asyncio.coroutine
    def test_full_request(self):
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = yield from self.client.request("GET", "/template/")
        assert 200 == request.status
        yield from request.text()
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        assert 2 == len(traces)
        assert 2 == len(traces[0])

        # request
        request_span = traces[0][0]
        assert_is_measured(request_span)
        TestSpan(request_span).assert_matches(
            name="aiohttp.request",
            service="aiohttp-web",
            resource="GET /template/",
        )

        # template
        template_span = traces[0][1]
        TestSpan(template_span).assert_matches(
            name="aiohttp.template",
            service="aiohttp-web",
            resource="aiohttp.template",
        )

        # client spans
        assert 4 == len(traces[1])  # these are tested via client tests

    @unittest_run_loop
    @asyncio.coroutine
    def test_multiple_full_request(self):
        # it should handle multiple requests using the same loop
        def make_requests():
            url = self.client.make_url("/delayed/")
            response = request.urlopen(str(url)).read().decode("utf-8")
            assert "Done" == response

        # blocking call executed in different threads
        threads = [threading.Thread(target=make_requests) for _ in range(10)]
        for t in threads:
            t.daemon = True
            t.start()

        # we should yield so that this loop can handle
        # threads' requests
        yield from asyncio.sleep(0.5)
        for t in threads:
            t.join(timeout=0.5)

        # the trace is created
        traces = self.tracer.writer.pop_traces()
        assert 10 == len(traces)
        assert 1 == len(traces[0])

    @unittest_run_loop
    @asyncio.coroutine
    @TraceTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a service name is specified by the user
            The aiohttp integration should use it as the service name
        """
        request = yield from self.client.request("GET", "/template/")
        yield from request.text()
        traces = self.tracer.writer.pop_traces()
        assert len(traces) == 2
        assert len(traces[0]) == 2

        request_span = traces[0][0]
        assert request_span.service == "mysvc"

        template_span = traces[0][1]
        assert template_span.service == "mysvc"

        # client spans
        assert len(traces[1]) == 4  # these are tested via client tests
