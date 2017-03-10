# stdlib
import unittest

# 3p
from nose.tools import eq_
import boto.ec2
import boto.s3
import boto.awslambda
import boto.sqs
from moto import mock_s3, mock_ec2, mock_lambda

# project
from ddtrace import Pin
from ddtrace.contrib.boto.patch import patch
from ddtrace.ext import http

# testing
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
        span = spans[0]
        eq_(span.get_tag('aws.operation'), "DescribeInstances")
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "POST")
        eq_(span.get_tag('aws.endpoint'), "ec2")
        eq_(span.get_tag('aws.region'), "us-west-2")

        # Create an instance
        ec2.run_instances(21)
        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.operation'), "RunInstances")
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "POST")
        eq_(span.get_tag('aws.endpoint'), "ec2")
        eq_(span.get_tag('aws.region'), "us-west-2")

        # Testing resource and service
        eq_(span.service, "test-boto-tracing.ec2")
        eq_(span.resource, "RunInstances.ec2.us-west-2")

    @mock_s3
    def test_s3_client(self):
        s3 = boto.s3.connect_to_region("us-east-1")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(s3)

        s3.get_all_buckets()
        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "GET")
        eq_(span.get_tag('aws.endpoint'), "s3")
        eq_(span.get_tag('aws.operation'), "get_all_buckets")

        # Create a bucket command
        s3.create_bucket("cheese")
        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "PUT")
        eq_(span.get_tag('aws.endpoint'), "s3")
        eq_(span.get_tag('aws.operation'), "create_bucket")

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "HEAD")
        eq_(span.get_tag('aws.endpoint'), "s3")
        eq_(span.get_tag('aws.operation'), "head_bucket")

        # Testing resource and service
        eq_(span.service, "test-boto-tracing.s3")
        eq_(span.resource, "head_bucket.s3")

    @mock_lambda
    def test_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(lamb)

        lamb.list_functions()
        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag(http.STATUS_CODE), "200")
        eq_(span.get_tag(http.METHOD), "GET")
        eq_(span.get_tag('aws.endpoint'), "lambda")
        eq_(span.get_tag('aws.region'), "us-east-2")
        eq_(span.get_tag('aws.operation'), "list_functions")

        # Testing resource and service
        eq_(span.service, "test-boto-tracing.lambda")
        eq_(span.resource, "list_functions.lambda.us-east-2")
