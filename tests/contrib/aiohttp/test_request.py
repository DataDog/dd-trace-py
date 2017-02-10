from nose.tools import eq_
from aiohttp.test_utils import unittest_run_loop

from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ddtrace.contrib.aiohttp.middlewares import TraceMiddleware

from .utils import TraceTestCase


class TestRequestTracing(TraceTestCase):
    """
    Ensures that the trace includes all traced components.
    """
    def enable_tracing(self):
        # enabled tracing:
        #   * middleware
        #   * templates
        TraceMiddleware(self.app, self.tracer)
        patch(tracer=self.tracer)

    def disable_tracing(self):
        unpatch()

    @unittest_run_loop
    async def test_full_request(self):
        # it should create a root span when there is a handler hit
        # with the proper tags
        request = await self.client.request('GET', '/template/')
        eq_(200, request.status)
        await request.text()
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
