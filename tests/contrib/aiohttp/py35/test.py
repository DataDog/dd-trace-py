import asyncio

from aiohttp import ClientSession
from aiohttp.test_utils import unittest_run_loop

from ddtrace import Pin
from ddtrace.contrib.asyncio.patch import patch as aio_patch, \
    unpatch as aio_unpatch
from ddtrace.contrib.aiohttp.patch import patch, unpatch
from ddtrace.settings import config
from ..utils import TraceTestCase
from ....utils.span import TestSpan


class AIOHttpTest(TraceTestCase):
    """Botocore integration testsuite"""

    def setUp(self):
        super(AIOHttpTest, self).setUp()
        # TODO: this needs to have a way to set this on an instance
        config.aiohttp_client['distributed_tracing_enabled'] = True
        patch()
        Pin.override(ClientSession, tracer=self.tracer)

    def tearDown(self):
        super(TraceTestCase, self).tearDown()
        unpatch()
        config.aiohttp_client['distributed_tracing_enabled'] = False
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

            self.assert_trace_count(1)

            spans = self.get_spans()
            assert 6 == len(spans)

            # outer span
            TestSpan(spans[-1]).assert_matches(
                name="foo",
                service=None,
                resource="foo",
                parent_id=None,
            )

            TestSpan(spans[-6]).assert_matches(
                name="ClientSession.request",
                service="aiohttp.client",
                resource="/",
                trace_id=spans[-1].trace_id,
                parent_id=spans[-1].span_id,
            )

            # TCPConnector.connect
            TestSpan(spans[-5]).assert_matches(
                name="TCPConnector.connect",
                service="aiohttp.client",
                resource="/",
                trace_id=spans[-6].trace_id,
                parent_id=spans[-6].span_id,
            )

            # TCPConnector._create_connection
            TestSpan(spans[-4]).assert_matches(
                name="TCPConnector._create_connection",
                service="aiohttp.client",
                resource="/",
                trace_id=spans[-5].trace_id,
                parent_id=spans[-5].span_id,
            )

            # client start span
            TestSpan(spans[-3]).assert_matches(
                name="ClientResponse.start",
                service="aiohttp.client",
                resource="/",
                trace_id=spans[-6].trace_id,
                parent_id=spans[-6].span_id,
            )

            # client start span
            TestSpan(spans[-2]).assert_matches(
                name="FlowControlStreamReader.read",
                service="aiohttp.client",
                resource="/",
                trace_id=spans[-6].trace_id,
                parent_id=spans[-6].span_id,
            )
        finally:
            aio_unpatch()
