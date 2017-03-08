# stdlib
import unittest

# 3p
from nose.tools import eq_
import botocore.session
from moto import mock_s3, mock_ec2, mock_lambda, mock_sqs, mock_kinesis

# project
from ddtrace import Pin
from ddtrace.contrib.botocore.patch import patch
from ddtrace.ext import http

# testing
from ...test_tracer import get_dummy_tracer


class BotocoreTest(unittest.TestCase):
    """Botocore integration testsuite"""

    TEST_SERVICE = "test-botocore-tracing"

    def setUp(self):
        patch()
        self.session = botocore.session.get_session()

    @mock_ec2
    def test_traced_client(self):

        ec2 = self.session.create_client('ec2', region_name='us-west-2')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(ec2)
        # Trying describe instances command
        ec2.describe_instances()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.agent'), "botocore")
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.endpoint'), 'ec2')
        eq_(span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.get_tag('retry_attempts'), '0')

        # Testing resource and service
        eq_(span.service, "test-botocore-tracing")
        eq_(span.resource, "DescribeInstances.ec2.us-west-2")

    @mock_s3
    def test_s3_client(self):
        s3 = self.session.create_client('s3', region_name='us-west-2')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(s3)

        # Listing buckets
        s3.list_buckets()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.endpoint'), "s3")
        eq_(span.get_tag('aws.operation'), 'ListBuckets')
        eq_(span.get_tag(http.STATUS_CODE), '200')

        try:
            s3.list_objects(bucket='mybucket')
        except Exception:
            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(span.error, 1)
            # test only work in py27, py34 returns another string
            # eq_(span.get_tag('botocore.args'), u"(u'ListObjects', {'bucket': 'mybucket'})")

    @mock_sqs
    def test_sqs_client(self):
        sqs = self.session.create_client('sqs', region_name='us-east-1')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(sqs)

        sqs.list_queues()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.endpoint'), "sqs")
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListQueues')
        eq_(span.get_tag(http.STATUS_CODE), '200')

    @mock_kinesis
    def test_kinesis_client(self):
        kinesis = self.session.create_client('kinesis', region_name='us-east-1')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(kinesis)

        kinesis.list_streams()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.endpoint'), "kinesis")
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListStreams')
        eq_(span.get_tag(http.STATUS_CODE), '200')

    @mock_lambda
    def test_lambda_client(self):
        lamb = self.session.create_client('lambda', region_name='us-east-1')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(lamb)

        lamb.list_functions()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.endpoint'), "lambda")
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListFunctions')
        eq_(span.get_tag(http.STATUS_CODE), '200')
