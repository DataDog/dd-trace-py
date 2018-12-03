# flake8: noqa
# DEV: Skip linting, we lint with Python 2, we'll get SyntaxErrors from `async`
from nose.tools import eq_

from ddtrace.contrib.aiobotocore.patch import patch, unpatch

from ..utils import aiobotocore_client
from ...asyncio.utils import AsyncioTestCase, mark_asyncio
from ....test_tracer import get_dummy_tracer


class AIOBotocoreTest(AsyncioTestCase):
    """Botocore integration testsuite"""
    def setUp(self):
        super(AIOBotocoreTest, self).setUp()
        patch()
        self.tracer = get_dummy_tracer()

    def tearDown(self):
        super(AIOBotocoreTest, self).tearDown()
        unpatch()
        self.tracer = None

    @mark_asyncio
    async def test_response_context_manager(self):
        # the client should call the wrapped __aenter__ and return the
        # object proxy
        with aiobotocore_client('s3', self.tracer) as s3:
            # prepare S3 and flush traces if any
            await s3.create_bucket(Bucket='tracing')
            await s3.put_object(Bucket='tracing', Key='apm', Body=b'')
            self.tracer.writer.pop_traces()
            # `async with` under test
            response = await s3.get_object(Bucket='tracing', Key='apm')
            async with response['Body'] as stream:
                await stream.read()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 2)
        eq_(len(traces[0]), 1)
        eq_(len(traces[1]), 1)

        span = traces[0][0]
        eq_(span.get_tag('aws.operation'), 'GetObject')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.s3')
        eq_(span.resource, 's3.getobject')

        read_span = traces[1][0]
        eq_(read_span.get_tag('aws.operation'), 'GetObject')
        eq_(read_span.get_tag('http.status_code'), '200')
        eq_(read_span.service, 'aws.s3')
        eq_(read_span.resource, 's3.getobject')
        eq_(read_span.name, 's3.command.read')
        # enforce parenting
        eq_(read_span.parent_id, span.span_id)
        eq_(read_span.trace_id, span.trace_id)
