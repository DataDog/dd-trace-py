# stdlib
import unittest

# 3p
from nose.tools import eq_
import botocore.session
from moto import mock_s3, mock_ec2, mock_lambda, mock_sqs, mock_kinesis, mock_kms

# project
from ddtrace import Pin
from ddtrace.contrib.botocore.patch import patch, unpatch
from ddtrace.ext import http

# testing
from tests.opentracer.utils import init_tracer
from ...test_tracer import get_dummy_tracer


class BotocoreTest(unittest.TestCase):
    """Botocore integration testsuite"""

    TEST_SERVICE = "test-botocore-tracing"

    def setUp(self):
        patch()
        self.session = botocore.session.get_session()

    def tearDown(self):
        unpatch()

    @mock_ec2
    def test_traced_client(self):

        ec2 = self.session.create_client('ec2', region_name='us-west-2')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(ec2)

        ec2.describe_instances()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(len(spans), 1)
        eq_(span.get_tag('aws.agent'), "botocore")
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.get_tag('retry_attempts'), '0')
        eq_(span.service, "test-botocore-tracing.ec2")
        eq_(span.resource, "ec2.describeinstances")
        eq_(span.name, "ec2.command")
        eq_(span.span_type, 'http')

    @mock_s3
    def test_s3_client(self):
        s3 = self.session.create_client('s3', region_name='us-west-2')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(s3)

        s3.list_buckets()
        s3.list_buckets()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(len(spans), 2)
        eq_(span.get_tag('aws.operation'), 'ListBuckets')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.service, "test-botocore-tracing.s3")
        eq_(span.resource, "s3.listbuckets")

        # testing for span error
        try:
            s3.list_objects(bucket='mybucket')
        except Exception:
            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(span.error, 1)
            eq_(span.resource, "s3.listobjects")

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
        eq_(len(spans), 1)
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListQueues')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.service, "test-botocore-tracing.sqs")
        eq_(span.resource, "sqs.listqueues")

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
        eq_(len(spans), 1)
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListStreams')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.service, "test-botocore-tracing.kinesis")
        eq_(span.resource, "kinesis.liststreams")

    @mock_kinesis
    def test_unpatch(self):
        kinesis = self.session.create_client('kinesis', region_name='us-east-1')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(kinesis)

        unpatch()

        kinesis.list_streams()
        spans = writer.pop()
        assert not spans, spans

    @mock_sqs
    def test_double_patch(self):
        sqs = self.session.create_client('sqs', region_name='us-east-1')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(sqs)

        patch()
        patch()

        sqs.list_queues()

        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)

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
        eq_(len(spans), 1)
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListFunctions')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.service, "test-botocore-tracing.lambda")
        eq_(span.resource, "lambda.listfunctions")

    @mock_kms
    def test_kms_client(self):
        kms = self.session.create_client('kms', region_name='us-east-1')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(len(spans), 1)
        eq_(span.get_tag('aws.region'), 'us-east-1')
        eq_(span.get_tag('aws.operation'), 'ListKeys')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.service, "test-botocore-tracing.kms")
        eq_(span.resource, "kms.listkeys")

        # checking for protection on sts against security leak
        eq_(span.get_tag('params'), None)

    @mock_ec2
    def test_traced_client_ot(self):
        """OpenTracing version of test_traced_client."""
        tracer = get_dummy_tracer()
        writer = tracer.writer
        ot_tracer = init_tracer('ec2_svc', tracer)

        with ot_tracer.start_active_span('ec2_op'):
            ec2 = self.session.create_client('ec2', region_name='us-west-2')
            Pin(service=self.TEST_SERVICE, tracer=tracer).onto(ec2)
            ec2.describe_instances()

        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)

        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.name, 'ec2_op')
        eq_(ot_span.service, 'ec2_svc')

        eq_(dd_span.get_tag('aws.agent'), "botocore")
        eq_(dd_span.get_tag('aws.region'), 'us-west-2')
        eq_(dd_span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(dd_span.get_tag(http.STATUS_CODE), '200')
        eq_(dd_span.get_tag('retry_attempts'), '0')
        eq_(dd_span.service, "test-botocore-tracing.ec2")
        eq_(dd_span.resource, "ec2.describeinstances")
        eq_(dd_span.name, "ec2.command")


if __name__ == '__main__':
    unittest.main()
