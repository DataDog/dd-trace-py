# stdlib
import asyncio
import asynctest
import os

# 3p
from nose.tools import eq_
import aiobotocore.session


# project
from ddtrace import Pin
from ddtrace.contrib.aiobotocore.patch import patch, unpatch
from ddtrace.ext import http


# testing
from ...test_tracer import get_dummy_tracer
from .utils import MotoService, MOTO_ENDPOINT_URL


class AIOBotocoreTest(asynctest.TestCase):
    """Botocore integration testsuite"""

    TEST_SERVICE = "test-aiobotocore-tracing"

    def setUp(self):
        patch()
        self.session = aiobotocore.session.get_session()
        os.environ['AWS_ACCESS_KEY_ID'] = 'dummy'
        os.environ['AWS_SECRET_ACCESS_KEY'] = 'dummy'

    def tearDown(self):
        unpatch()
        self.session = None
        del os.environ['AWS_ACCESS_KEY_ID']
        del os.environ['AWS_SECRET_ACCESS_KEY']

    @MotoService('ec2')
    @asyncio.coroutine
    def test_traced_client(self):
        ec2 = self.session.create_client('ec2', region_name='us-west-2', endpoint_url=MOTO_ENDPOINT_URL)
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(ec2)

        yield from ec2.describe_instances()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(len(spans), 1)
        eq_(span.get_tag('aws.agent'), "aiobotocore")
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.get_tag('retry_attempts'), '0')
        eq_(span.service, "test-aiobotocore-tracing.ec2")
        eq_(span.resource, "ec2.describeinstances")
        eq_(span.name, "ec2.command")

        ec2.close()

    @MotoService('s3')
    @asyncio.coroutine
    def test_s3_client(self):
        s3 = self.session.create_client('s3', region_name='us-west-2', endpoint_url=MOTO_ENDPOINT_URL)
        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(s3)

            yield from s3.list_buckets()
            yield from s3.list_buckets()

            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(len(spans), 2)
            eq_(span.get_tag('aws.operation'), 'ListBuckets')
            eq_(span.get_tag(http.STATUS_CODE), '200')
            eq_(span.service, "test-aiobotocore-tracing.s3")
            eq_(span.resource, "s3.listbuckets")

            # testing for span error
            try:
                yield from s3.list_objects(bucket='mybucket')
            except Exception:
                spans = writer.pop()
                assert spans
                span = spans[0]
                eq_(span.error, 1)
                eq_(span.resource, "s3.listobjects")
        finally:
            s3.close()

    @MotoService('sqs')
    @asyncio.coroutine
    def test_sqs_client(self):
        sqs = self.session.create_client('sqs', region_name='us-east-1', endpoint_url=MOTO_ENDPOINT_URL)
        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(sqs)

            yield from sqs.list_queues()

            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(len(spans), 1)
            eq_(span.get_tag('aws.region'), 'us-east-1')
            eq_(span.get_tag('aws.operation'), 'ListQueues')
            eq_(span.get_tag(http.STATUS_CODE), '200')
            eq_(span.service, "test-aiobotocore-tracing.sqs")
            eq_(span.resource, "sqs.listqueues")
        finally:
            sqs.close()

    @MotoService('kinesis')
    @asyncio.coroutine
    def test_kinesis_client(self):
        kinesis = self.session.create_client('kinesis', region_name='us-east-1', endpoint_url=MOTO_ENDPOINT_URL)

        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(kinesis)

            yield from kinesis.list_streams()

            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(len(spans), 1)
            eq_(span.get_tag('aws.region'), 'us-east-1')
            eq_(span.get_tag('aws.operation'), 'ListStreams')
            eq_(span.get_tag(http.STATUS_CODE), '200')
            eq_(span.service, "test-aiobotocore-tracing.kinesis")
            eq_(span.resource, "kinesis.liststreams")
        finally:
            kinesis.close()

    @MotoService('kinesis')
    @asyncio.coroutine
    def test_unpatch(self):
        kinesis = self.session.create_client('kinesis', region_name='us-east-1', endpoint_url=MOTO_ENDPOINT_URL)
        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(kinesis)

            unpatch()

            yield from kinesis.list_streams()
            spans = writer.pop()
            assert not spans, spans
        finally:
            kinesis.close()

    @MotoService('sqs')
    @asyncio.coroutine
    def test_double_patch(self):
        sqs = self.session.create_client('sqs', region_name='us-east-1', endpoint_url=MOTO_ENDPOINT_URL)

        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(sqs)

            patch()
            patch()

            yield from sqs.list_queues()

            spans = writer.pop()
            assert spans
            eq_(len(spans), 1)
        finally:
            sqs.close()

    # TODO: could not get lambda working with moto server as it uses a url with
    #  \d{} which gets escaped seemingly incorrectly to flask
    # @MotoService('lambda')
    # @asyncio.coroutine
    # def test_lambda_client(self):
    #     lamb = self.session.create_client('lambda', region_name='us-east-1', endpoint_url=MOTO_ENDPOINT_URL)
    #     try:
    #         tracer = get_dummy_tracer()
    #         writer = tracer.writer
    #         Pin(service=self.TEST_SERVICE, tracer=tracer).onto(lamb)
    #
    #         # https://github.com/spulec/moto/issues/906
    #         yield from lamb.list_functions(MaxItems=5)
    #
    #         spans = writer.pop()
    #         assert spans
    #         span = spans[0]
    #         eq_(len(spans), 1)
    #         eq_(span.get_tag('aws.region'), 'us-east-1')
    #         eq_(span.get_tag('aws.operation'), 'ListFunctions')
    #         eq_(span.get_tag(http.STATUS_CODE), '200')
    #         eq_(span.service, "test-aiobotocore-tracing.lambda")
    #         eq_(span.resource, "lambda.listfunctions")
    #     finally:
    #         lamb.close()

    @MotoService('kms')
    @asyncio.coroutine
    def test_kms_client(self):
        kms = self.session.create_client('kms', region_name='us-east-1', endpoint_url=MOTO_ENDPOINT_URL)
        try:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(kms)

            yield from kms.list_keys(Limit=21)

            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(len(spans), 1)
            eq_(span.get_tag('aws.region'), 'us-east-1')
            eq_(span.get_tag('aws.operation'), 'ListKeys')
            eq_(span.get_tag(http.STATUS_CODE), '200')
            eq_(span.service, "test-aiobotocore-tracing.kms")
            eq_(span.resource, "kms.listkeys")

            # checking for protection on sts against security leak
            eq_(span.get_tag('params'), None)
        finally:
            kms.close()

if __name__ == '__main__':
    asynctest.main()
