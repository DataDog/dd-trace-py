import threading
import asyncio
import aiohttp
import aiohttp_jinja2

from urllib import request
from nose.tools import eq_
from aiohttp.test_utils import unittest_run_loop

from ddtrace.pin import Pin
from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ddtrace.contrib.aiohttp.middlewares import trace_app

from .utils import TraceTestCase


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
        trace_app(self.app, self.tracer, enable_distributed=True)
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
        eq_(3, len(traces))

        # client request span
        eq_(2, len(traces[1]))
        client_request_span = traces[1][0]
        root_span_id = client_request_span.span_id
        root_trace_id = client_request_span.trace_id
        eq_(None, client_request_span.parent_id)
        eq_('aiohttp.client', client_request_span.service)
        eq_('ClientSession.request', client_request_span.name)
        eq_('/', client_request_span.resource)

        # client start span
        client_start_span = traces[1][1]
        eq_(root_span_id, client_start_span.parent_id)
        eq_(root_trace_id, client_start_span.trace_id)
        eq_('aiohttp.client', client_start_span.service)
        eq_('ClientResponse.start', client_start_span.name)
        eq_('/', client_start_span.resource)

        # web server request span
        eq_(1, len(traces[0]))
        server_request_span = traces[0][0]
        eq_(root_span_id, server_request_span.parent_id)
        eq_(root_trace_id, server_request_span.trace_id)
        eq_('aiohttp-web', server_request_span.service)
        eq_('aiohttp.request', server_request_span.name)
        eq_('/', server_request_span.resource)

        # client read span
        eq_(1, len(traces[2]))
        read_span = traces[2][0]
        eq_(root_span_id, read_span.parent_id)
        eq_(root_trace_id, read_span.trace_id)
        eq_('aiohttp.client', read_span.service)
        eq_('ClientResponse.read', read_span.name)
        eq_('/', read_span.resource)

    @unittest_run_loop
    @asyncio.coroutine
    def test_full_request(self):
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = yield from self.client.request('GET', '/template/')
        eq_(200, request.status)
        yield from request.text()
        # the trace is created
        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        request_span = traces[0][0]
        template_span = traces[0][1]
        # request
        eq_('aiohttp-web', request_span.service)
        eq_('aiohttp.request', request_span.name)
        eq_('/template/', request_span.resource)
        # template
        eq_('aiohttp-web', template_span.service)
        eq_('aiohttp.template', template_span.name)
        eq_('aiohttp.template', template_span.resource)

    @unittest_run_loop
    @asyncio.coroutine
    def test_multiple_full_request(self):
        # it should handle multiple requests using the same loop
        def make_requests():
            url = self.client.make_url('/delayed/')
            response = request.urlopen(str(url)).read().decode('utf-8')
            eq_('Done', response)

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
        eq_(10, len(traces))
        eq_(1, len(traces[0]))
