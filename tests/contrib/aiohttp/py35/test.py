# flake8: noqa
# DEV: Skip linting, we lint with Python 2, we'll get SyntaxErrors from `async`
import asyncio

from aiohttp import ClientSession
from aiohttp.test_utils import unittest_run_loop

from ddtrace.contrib.asyncio.patch import patch as aio_patch, \
    unpatch as aio_unpatch
from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ..utils import TraceTestCase
from ....utils.span import TestSpan


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
                        assert request.status == 200
                        await request.text()
                        # the trace is created

                await asyncio.wait_for(doit(), 20)

            traces = self.tracer.writer.pop_traces()
            self.assert_trace_count(1)

            # outer span
            assert len(traces[1]) == 1
            root_span = traces[1][0]
            root_span_id = root_span.span_id

            TestSpan(root_span).assert_matches(
                name="foo",
                service=None,
                resource="foo",
                parent_id=None,
            )

            assert len(traces[0]) == 4
            client_request_span = traces[0][0]
            TestSpan(client_request_span).assert_matches(
                name="ClientSession.request",
                service="aiohttp.client",
                resource="/",
                parent_id=root_span_id,
            )

            # TCPConnector.connect
            connector_connect_span = traces[0][1]
            TestSpan(connector_connect_span).assert_matches(
                name="TCPConnector.connect",
                service="aiohttp.client",
                resource="/",
                parent_id=client_request_span.span_id,
                trace_id=client_request_span.trace_id,
            )

            # TCPConnector._create_connection
            connector_create_connection_span = traces[0][2]
            TestSpan(connector_create_connection_span).assert_matches(
                name="TCPConnector._create_connection",
                service="aiohttp.client",
                resource="/",
                parent_id=connector_connect_span.span_id,
                trace_id=connector_connect_span.trace_id,
            )

            # client start span
            client_start_span = traces[0][3]
            TestSpan(client_start_span).assert_matches(
                name="ClientResponse.start",
                service="aiohttp.client",
                resource="/",
                parent_id=client_request_span.span_id,
                trace_id=client_request_span.trace_id,
            )
        finally:
            aio_unpatch()
