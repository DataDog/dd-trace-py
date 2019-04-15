# flake8: noqa
import threading
import asyncio
import aiohttp_jinja2

from urllib import request
from aiohttp.test_utils import unittest_run_loop

from ddtrace.pin import Pin
from ddtrace.provider import DefaultContextProvider
from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ddtrace.contrib.aiohttp.middlewares import trace_app

from .utils import TraceTestCase


class TestAiohttpSafety(TraceTestCase):
    """
    Ensure that if the ``AsyncioTracer`` is not properly configured,
    bad traces are produced but the ``Context`` object will not
    leak memory.
    """
    def enable_tracing(self):
        # aiohttp TestCase with the wrong context provider
        trace_app(self.app, self.tracer)
        patch()
        Pin.override(aiohttp_jinja2, tracer=self.tracer)
        self.tracer.configure(context_provider=DefaultContextProvider())

    def disable_tracing(self):
        unpatch()

    @unittest_run_loop
    @asyncio.coroutine
    def test_full_request(self):
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = yield from self.client.request('GET', '/template/')
        assert 200 == request.status
        yield from request.text()
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])
        request_span = traces[0][0]
        template_span = traces[0][1]
        # request
        assert 'aiohttp-web' == request_span.service
        assert 'aiohttp.request' == request_span.name
        assert 'GET /template/' == request_span.resource
        # template
        assert 'aiohttp-web' == template_span.service
        assert 'aiohttp.template' == template_span.name
        assert 'aiohttp.template' == template_span.resource

    @unittest_run_loop
    @asyncio.coroutine
    def test_multiple_full_request(self):
        # it should produce a wrong trace, but the Context must
        # be finished
        def make_requests():
            url = self.client.make_url('/delayed/')
            response = request.urlopen(str(url)).read().decode('utf-8')
            assert 'Done' == response

        # blocking call executed in different threads
        ctx = self.tracer.get_call_context()
        threads = [threading.Thread(target=make_requests) for _ in range(10)]
        for t in threads:
            t.start()

        for t in threads:
            # we should yield so that this loop can handle
            # threads' requests
            yield from asyncio.sleep(0.1)
            t.join(0.1)

        # the trace is wrong but the Context is finished
        traces = self.tracer.writer.pop_traces()
        assert 1 == len(traces)
        assert 10 == len(traces[0])
        assert 0 == len(ctx._trace)
