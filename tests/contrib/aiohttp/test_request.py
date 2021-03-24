import asyncio
import threading
from urllib import request

from aiohttp.test_utils import unittest_run_loop
import aiohttp_jinja2

from ddtrace import config
from ddtrace.contrib.aiohttp.middlewares import trace_app
from ddtrace.contrib.aiohttp.patch import patch
from ddtrace.contrib.aiohttp.patch import unpatch
from ddtrace.pin import Pin
from tests.utils import assert_is_measured

from .utils import TraceTestCase


class TestRequestTracing(TraceTestCase):
    """
    Ensures that the trace includes all traced components.
    """

    def enable_tracing(self):
        # enabled tracing:
        #   * middleware
        #   * templates
        trace_app(self.app, self.tracer)
        patch()
        Pin.override(aiohttp_jinja2, tracer=self.tracer)

    def disable_tracing(self):
        unpatch()

    @unittest_run_loop
    @asyncio.coroutine
    def test_full_request(self):
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = yield from self.client.request("GET", "/template/")
        assert 200 == request.status
        yield from request.text()
        # the trace is created
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])
        request_span = traces[0][0]
        assert_is_measured(request_span)

        template_span = traces[0][1]
        # request
        assert "aiohttp-web" == request_span.service
        assert "aiohttp.request" == request_span.name
        assert "GET /template/" == request_span.resource
        # template
        assert "aiohttp-web" == template_span.service
        assert "aiohttp.template" == template_span.name
        assert "aiohttp.template" == template_span.resource

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
        traces = self.pop_traces()
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
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])

        request_span = traces[0][0]
        assert request_span.service == "mysvc"

        template_span = traces[0][1]
        assert template_span.service == "mysvc"

    @unittest_run_loop
    @asyncio.coroutine
    def test_http_request_header_tracing(self):
        config.aiohttp.http.trace_headers(["my-header"])
        request = yield from self.client.request("GET", "/", headers={"my-header": "my_value"})
        yield from request.text()

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert request_span.service == "aiohttp-web"
        assert request_span.get_tag("http.request.headers.my-header") == "my_value"

    @unittest_run_loop
    @asyncio.coroutine
    def test_http_response_header_tracing(self):
        config.aiohttp.http.trace_headers(["my-response-header"])
        request = yield from self.client.request("GET", "/response_headers/")
        yield from request.text()

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

        request_span = traces[0][0]
        assert request_span.service == "aiohttp-web"
        assert request_span.get_tag("http.response.headers.my-response-header") == "my_response_value"
