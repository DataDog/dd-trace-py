# flake8: noqa
# DEV: Skip linting, we lint with Python 2, we'll get SyntaxErrors from `async`
import asyncio

from nose.tools import eq_
from aiohttp import ClientSession
from aiohttp.test_utils import unittest_run_loop

from ddtrace.contrib.asyncio.patch import patch as aio_patch, \
    unpatch as aio_unpatch
from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ..utils import TraceTestCase


class AIOHttpTest(TraceTestCase):
    """Botocore integration testsuite"""

    def setUp(self):
        super(AIOHttpTest, self).setUp()
        patch(self.tracer, enable_distributed=True)

    def tearDown(self):
        super(TraceTestCase, self).tearDown()
        unpatch()
        self.tracer = None

    @unittest_run_loop
    async def test_wait_for_full_request(self):
        aio_patch()

        session = ClientSession()
        url = self.client.make_url('/')

        try:
            with self.tracer.trace("foo"):
                async def doit():
                    async with session.get(url) as request:
                        eq_(200, request.status)
                        await request.text()
                        # the trace is created

                await asyncio.wait_for(doit(), 20)

            traces = self.tracer.writer.pop_traces()
            eq_(2, len(traces))

            # outer span
            eq_(1, len(traces[1]))
            root_span = traces[1][0]
            root_span_id = root_span.span_id

            eq_(None, root_span.parent_id)
            eq_(None, root_span.service)
            eq_('foo', root_span.name)
            eq_('foo', root_span.resource)

            eq_(4, len(traces[0]))
            client_request_span = traces[0][0]
            eq_(root_span_id, client_request_span.parent_id)
            eq_('aiohttp.client', client_request_span.service)
            eq_('ClientSession.request', client_request_span.name)
            eq_('/', client_request_span.resource)

            # TCPConnector.connect
            connector_connect_span = traces[0][1]
            eq_(client_request_span.span_id, connector_connect_span.parent_id)
            eq_(client_request_span.trace_id, connector_connect_span.trace_id)
            eq_('aiohttp.client', connector_connect_span.service)
            eq_('TCPConnector.connect', connector_connect_span.name)
            eq_('/', connector_connect_span.resource)

            # TCPConnector._create_connection
            connector_create_connection_span = traces[0][2]
            eq_(connector_connect_span.span_id,
                connector_create_connection_span.parent_id)
            eq_(connector_connect_span.trace_id,
                connector_create_connection_span.trace_id)
            eq_('aiohttp.client', connector_create_connection_span.service)
            eq_('TCPConnector._create_connection',
                connector_create_connection_span.name)
            eq_('/', connector_create_connection_span.resource)

            # client start span
            client_start_span = traces[0][3]
            eq_(client_request_span.span_id, client_start_span.parent_id)
            eq_(client_request_span.trace_id, client_start_span.trace_id)
            eq_('aiohttp.client', client_start_span.service)
            eq_('ClientResponse.start', client_start_span.name)
            eq_('/', client_start_span.resource)
        finally:
            aio_unpatch()
