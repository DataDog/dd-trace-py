# stdlib
import unittest

# 3p
from nose.tools import eq_
import boto.ec2
import boto.s3

# project
from ddtrace.contrib.boto.patch import patch

# testing
from ...test_tracer import get_dummy_tracer


class BotoTest(unittest.TestCase):
    """Botocore integration testsuite"""

    def setUp(self):
        patch()

    def test_ec2_client(self):
                    ec2 = boto.ec2.connect_to_region("us-west-2")
                    ec2.datadog_tracer = get_dummy_tracer()
                    writer = ec2.datadog_tracer.writer

                    # Trying describe instances command
                    ec2.get_all_instances()

                    spans = writer.pop()
                    assert spans
                    span = spans[0]
                    eq_(span.get_tag('aws.operation'), "DescribeInstances")
                    eq_(span.get_tag('http_status'), "200")
                    eq_(span.get_tag('http_method'), "POST")
                    eq_(span.get_tag('aws.endpoint'), "ec2")
                    eq_(span.get_tag('aws.region'), "us-west-2")

    def test_s3_client(self):
        s3 = boto.s3.connect_to_region("us-east-1")
        s3.datadog_tracer = get_dummy_tracer()
        writer = s3.datadog_tracer.writer

        # Trying describe instances command
        s3.get_all_buckets()
        print s3.__dict__
        spans = writer.pop()
        assert spans
        span = spans[0]
        #eq_(span.get_tag('aws.operation'), "DescribeInstances")
        eq_(span.get_tag('http_status'), "200")
        eq_(span.get_tag('http_method'), "GET")
        eq_(span.get_tag('aws.endpoint'), "s3")
