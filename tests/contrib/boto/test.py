# stdlib
import unittest

# 3p
from nose.tools import eq_
import boto.ec2
import boto.s3
import boto.awslambda
import boto.sqs
import boto.kms
import boto.sts
import boto.elasticache
from moto import mock_s3, mock_ec2, mock_lambda, mock_sts

# project
from ddtrace import Pin
from ddtrace.contrib.boto.patch import patch, unpatch
from ddtrace.ext import http

# testing
from unittest import skipUnless
from tests.opentracer.utils import init_tracer
from ...test_tracer import get_dummy_tracer


class BotoTest(unittest.TestCase):
    """Botocore integration testsuite"""

    TEST_SERVICE = "test-boto-tracing"

    def setUp(self):
        patch()

    @mock_ec2
    def test_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(ec2)

        ec2.get_all_instances()
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.get_tag('aws.operation'), "DescribeInstances")
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "POST")
        eq_(span.get_tag('aws.region'), "us-west-2")

        # Create an instance
        ec2.run_instances(21)
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.get_tag('aws.operation'), "RunInstances")
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "POST")
        eq_(span.get_tag('aws.region'), "us-west-2")
        eq_(span.service, "test-boto-tracing.ec2")
        eq_(span.resource, "ec2.runinstances")
        eq_(span.name, "ec2.command")

    @mock_s3
    def test_s3_client(self):
        s3 = boto.s3.connect_to_region("us-east-1")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(s3)

        s3.get_all_buckets()
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "GET")
        eq_(span.get_tag('aws.operation'), "get_all_buckets")

        # Create a bucket command
        s3.create_bucket("cheese")
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "PUT")
        eq_(span.get_tag('path'), '/')
        eq_(span.get_tag('aws.operation'), "create_bucket")

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "HEAD")
        eq_(span.get_tag('aws.operation'), "head_bucket")
        eq_(span.service, "test-boto-tracing.s3")
        eq_(span.resource, "s3.head")
        eq_(span.name, "s3.command")

        # Checking for resource incase of error
        try:
            s3.get_bucket("big_bucket")
        except Exception:
            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(span.resource, "s3.head")

    @mock_lambda
    def test_unpatch(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(lamb)
        unpatch()

        # multiple calls
        lamb.list_functions()
        spans = writer.pop()
        assert not spans, spans

    @mock_s3
    def test_double_patch(self):
        s3 = boto.s3.connect_to_region("us-east-1")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(s3)

        patch()
        patch()

        # Get the created bucket
        s3.create_bucket("cheese")
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)

    @mock_lambda
    def test_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(lamb)

        # multiple calls
        lamb.list_functions()
        lamb.list_functions()
        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "GET")
        eq_(span.get_tag('aws.region'), "us-east-2")
        eq_(span.get_tag('aws.operation'), "list_functions")
        eq_(span.service, "test-boto-tracing.lambda")
        eq_(span.resource, "lambda.get")

    @mock_sts
    def test_sts_client(self):
        sts = boto.sts.connect_to_region('us-west-2')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'GetFederationToken')
        eq_(span.service, "test-boto-tracing.sts")
        eq_(span.resource, "sts.getfederationtoken")

        # checking for protection on sts against security leak
        eq_(span.get_tag('args.path'), None)

    @skipUnless(
        False,
    "Test to reproduce the case where args sent to patched function are None, can't be mocked: needs AWS crendentials")
    def test_elasticache_client(self):
        elasticache = boto.elasticache.connect_to_region('us-west-2')
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(elasticache)

        elasticache.describe_cache_clusters()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.service, "test-boto-tracing.elasticache")
        eq_(span.resource, "elasticache")

    @mock_ec2
    def test_ec2_client_ot(self):
        """OpenTracing compatibility check of the test_ec2_client test."""

        ec2 = boto.ec2.connect_to_region("us-west-2")
        tracer = get_dummy_tracer()
        ot_tracer = init_tracer('my_svc', tracer)
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(ec2)

        with ot_tracer.start_active_span('ot_span'):
            ec2.get_all_instances()
        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.resource, "ot_span")
        eq_(dd_span.get_tag('aws.operation'), "DescribeInstances")
        eq_(dd_span.get_tag(http.STATUS_CODE), "200")
        eq_(dd_span.get_tag(http.METHOD), "POST")
        eq_(dd_span.get_tag('aws.region'), "us-west-2")

        with ot_tracer.start_active_span('ot_span'):
            ec2.run_instances(21)
        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(dd_span.get_tag('aws.operation'), "RunInstances")
        eq_(dd_span.get_tag(http.STATUS_CODE), "200")
        eq_(dd_span.get_tag(http.METHOD), "POST")
        eq_(dd_span.get_tag('aws.region'), "us-west-2")
        eq_(dd_span.service, "test-boto-tracing.ec2")
        eq_(dd_span.resource, "ec2.runinstances")
        eq_(dd_span.name, "ec2.command")
