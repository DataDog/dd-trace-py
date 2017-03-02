# stdlib
import unittest

# 3p
from nose.tools import eq_
import botocore.session

# project
from ddtrace.contrib.botocore.patch import patch
from ddtrace.ext import http

# testing
from ...test_tracer import get_dummy_tracer


class BotocoreTest(unittest.TestCase):
    """Botocore integration testsuite"""

    def setUp(self):
        patch()
        self.session = botocore.session.get_session()

    def test_traced_client(self):

        client = self.session.create_client('ec2', region_name='us-west-2')
        client.datadog_tracer = get_dummy_tracer()
        writer = client.datadog_tracer.writer

        # Trying describe instances command
        client.describe_instances()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.agent'), "botocore")
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.endpoint'), 'ec2')
        eq_(span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(span.get_tag(http.STATUS_CODE), '200')
        eq_(span.get_tag('retry_attempts'), '0')

    def test_s3_client(self):
        client = self.session.create_client('s3', region_name='us-west-2')
        client.datadog_tracer = get_dummy_tracer()
        writer = client.datadog_tracer.writer

        # Listing buckets
        client.list_buckets()

        spans = writer.pop()
        assert spans
        span = spans[0]
        eq_(span.get_tag('aws.endpoint'), "s3")
        eq_(span.get_tag('aws.operation'), 'ListBuckets')
        eq_(span.get_tag(http.STATUS_CODE), '200')
