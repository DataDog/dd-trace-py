import base64
import datetime
import io
import json
import sys
import unittest
import zipfile

import botocore.exceptions
import botocore.session
import mock
from moto import mock_dynamodb
from moto import mock_ec2
from moto import mock_events
from moto import mock_kinesis
from moto import mock_kms
from moto import mock_lambda
from moto import mock_s3
from moto import mock_sns
from moto import mock_sqs
from moto import mock_stepfunctions
import pytest

from tests.utils import get_128_bit_trace_id_from_headers


# Older version of moto used kinesis to mock firehose
try:
    from moto import mock_firehose
except ImportError:
    from moto import mock_kinesis as mock_firehose

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.botocore.patch import _patch_submodules
from ddtrace.contrib.botocore.patch import patch
from ddtrace.contrib.botocore.patch import unpatch
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.internal.utils.version import parse_version
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code


# Parse botocore.__version_ from "1.9.0" to (1, 9, 0)
BOTOCORE_VERSION = parse_version(botocore.__version__)


def get_zip_lambda():
    code = """
def lambda_handler(event, context):
    return event
"""
    zip_output = io.BytesIO()
    zip_file = zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED)
    zip_file.writestr("lambda_function.py", code)
    zip_file.close()
    zip_output.seek(0)
    return zip_output.read()


class BotocoreTest(TracerTestCase):
    """Botocore integration testsuite"""

    TEST_SERVICE = "test-botocore-tracing"

    @mock_sqs
    def setUp(self):
        patch()
        _patch_submodules(True)

        self.session = botocore.session.get_session()
        self.session.set_credentials(access_key="access-key", secret_key="secret-key")

        self.queue_name = "Test"
        self.sqs_client = self.session.create_client(
            "sqs", region_name="us-east-1", endpoint_url="http://localhost:4566"
        )
        for queue_url in self.sqs_client.list_queues().get("QueueUrls", []):
            self.sqs_client.delete_queue(QueueUrl=queue_url)

        self.sqs_test_queue = self.sqs_client.create_queue(QueueName=self.queue_name)

        super(BotocoreTest, self).setUp()

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(botocore.parsers.ResponseParser)

    def tearDown(self):
        super(BotocoreTest, self).tearDown()

        unpatch()
        self.sqs_client.delete_queue(QueueUrl=self.queue_name)

    @mock_ec2
    @mock_s3
    def test_patch_submodules(self):
        _patch_submodules(["s3"])
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        assert spans == []

        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(s3)

        s3.list_buckets()
        s3.list_buckets()

        spans = self.get_spans()
        assert spans

    @mock_ec2
    def test_traced_client(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert_is_measured(span)
        assert span.get_tag("aws.agent") == "botocore"
        assert span.get_tag("aws.region") == "us-west-2"
        assert span.get_tag("region") == "us-west-2"
        assert span.get_tag("aws.operation") == "DescribeInstances"
        assert span.get_tag("aws.requestid") == "fdcdcab1-ae5c-489e-9c33-4637c5dda355"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_span_http_status_code(span, 200)
        assert span.get_metric("retry_attempts") == 0
        assert span.service == "test-botocore-tracing.ec2"
        assert span.resource == "ec2.describeinstances"
        assert span.name == "ec2.command"
        assert span.span_type == "http"
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_ec2_call_default(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin.get_from(ec2).clone(tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        span = spans[0]
        assert span.service == "aws.ec2", "Expected 'aws.ec2' but got {}".format(span.service)
        assert span.name == "ec2.command"

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_ec2_call_v0(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin.get_from(ec2).clone(tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        span = spans[0]
        assert span.service == "aws.ec2", "Expected 'aws.ec2' but got {}".format(span.service)
        assert span.name == "ec2.command"

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_ec2_call_v1(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin.get_from(ec2).clone(tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        span = spans[0]
        assert span.service == "mysvc", "Expected 'mysvc' but got {}".format(span.service)
        assert span.name == "aws.ec2.request"

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_ec2_call_default(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin.get_from(ec2).clone(tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        span = spans[0]
        assert span.service == "aws.ec2", "Expected 'aws.ec2' but got {}".format(span.service)
        assert span.name == "ec2.command"

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_ec2_call_v0(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin.get_from(ec2).clone(tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        span = spans[0]
        assert span.service == "aws.ec2", "Expected 'aws.ec2' but got {}".format(span.service)
        assert span.name == "ec2.command"

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_ec2_call_v1(self):
        ec2 = self.session.create_client("ec2", region_name="us-west-2")
        Pin.get_from(ec2).clone(tracer=self.tracer).onto(ec2)

        ec2.describe_instances()

        spans = self.get_spans()
        span = spans[0]
        assert (
            span.service == DEFAULT_SPAN_SERVICE_NAME
        ), "Expected 'internal.schema.DEFAULT_SPAN_SERVICE_NAME' but got {}".format(span.service)
        assert span.name == "aws.ec2.request"

    @mock_ec2
    def test_traced_client_analytics(self):
        with self.override_config("botocore", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            ec2 = self.session.create_client("ec2", region_name="us-west-2")
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(ec2)
            ec2.describe_instances()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    @pytest.mark.skipif(
        PYTHON_VERSION_INFO < (3, 8),
        reason="Skipping for older py versions whose latest supported moto versions don't have the right dynamodb api",
    )
    @mock_dynamodb
    def test_dynamodb_put_get(self):
        ddb = self.session.create_client("dynamodb", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(ddb)

        with self.override_config("botocore", dict(instrument_internals=True)):
            ddb.create_table(
                TableName="foobar",
                AttributeDefinitions=[{"AttributeName": "myattr", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "myattr", "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            )
            ddb.put_item(TableName="foobar", Item={"myattr": {"S": "baz"}})
            ddb.get_item(TableName="foobar", Key={"myattr": {"S": "baz"}})

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 6
        assert_is_measured(span)
        assert span.get_tag("aws.operation") == "CreateTable"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.dynamodb"
        assert span.resource == "dynamodb.createtable"

        span = spans[1]
        assert span.name == "botocore.parsers.parse"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert span.service == "test-botocore-tracing.dynamodb"
        assert span.resource == "botocore.parsers.parse"

    @mock_s3
    def test_s3_client(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(s3)

        s3.list_buckets()
        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert_is_measured(span)
        assert span.get_tag("aws.operation") == "ListBuckets"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.s3"
        assert span.resource == "s3.listbuckets"

        # testing for span error
        self.reset()
        try:
            s3.list_objects(bucket="mybucket")
        except Exception:
            spans = self.get_spans()
            assert spans
            span = spans[0]
            assert span.error == 1
            assert span.resource == "s3.listobjects"

    @mock_s3
    def test_s3_head_404_default(self):
        """
        By default we do not attach exception information to s3 HeadObject
        API calls with a 404 response
        """
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(s3)

        # We need a bucket for this test
        s3.create_bucket(Bucket="test", CreateBucketConfiguration=dict(LocationConstraint="us-west-2"))
        try:
            with pytest.raises(botocore.exceptions.ClientError):
                s3.head_object(Bucket="test", Key="unknown")
        finally:
            # Make sure to always delete the bucket after we are done
            s3.delete_bucket(Bucket="test")

        spans = self.get_spans()
        assert len(spans) == 3

        head_object = spans[1]
        assert head_object.name == "s3.command"
        assert head_object.resource == "s3.headobject"
        assert head_object.error == 0
        for t in (ERROR_MSG, ERROR_STACK, ERROR_TYPE):
            assert head_object.get_tag(t) is None

    @mock_s3
    def test_s3_head_404_as_errors(self):
        """
        When add 404 as a error status for "s3.headobject" operation
            we attach exception information to S3 HeadObject 404 responses
        """
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(s3)

        # We need a bucket for this test
        s3.create_bucket(Bucket="test", CreateBucketConfiguration=dict(LocationConstraint="us-west-2"))

        config.botocore.operations["s3.headobject"].error_statuses = "404,500-599"
        try:
            with pytest.raises(botocore.exceptions.ClientError):
                s3.head_object(Bucket="test", Key="unknown")
        finally:
            # Make sure we reset the config when we are done
            del config.botocore.operations["s3.headobject"]

            # Make sure to always delete the bucket after we are done
            s3.delete_bucket(Bucket="test")

        spans = self.get_spans()
        assert len(spans) == 3

        head_object = spans[1]
        assert head_object.name == "s3.command"
        assert head_object.resource == "s3.headobject"
        assert head_object.error == 1
        for t in (ERROR_MSG, ERROR_STACK, ERROR_TYPE):
            assert head_object.get_tag(t) is not None

    def _test_s3_put(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(s3)
        params = {
            "Bucket": "mybucket",
            "CreateBucketConfiguration": {
                "LocationConstraint": "us-west-2",
            },
        }
        s3.create_bucket(**params)
        params = dict(Key="foo", Bucket="mybucket", Body=b"bar")
        s3.put_object(**params)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.operation") == "CreateBucket"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.s3"
        assert span.resource == "s3.createbucket"
        assert spans[1].get_tag("aws.operation") == "PutObject"
        assert spans[1].get_tag("component") == "botocore"
        assert spans[1].get_tag("span.kind"), "client"
        assert spans[1].resource == "s3.putobject"
        return spans[1]

    @mock_s3
    def test_s3_put(self):
        span = self._test_s3_put()
        assert span.get_tag("aws.s3.bucket_name") == "mybucket"
        assert span.get_tag("bucketname") == "mybucket"

    @mock_s3
    def test_s3_put_no_params(self):
        with self.override_config("botocore", dict(tag_no_params=True)):
            span = self._test_s3_put()
            assert span.get_tag("aws.s3.bucket_name") is None
            assert span.get_tag("bucketname") is None
            assert span.get_tag("params.Key") is None
            assert span.get_tag("params.Bucket") is None
            assert span.get_tag("params.Body") is None
            assert span.get_tag("component") == "botocore"

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_SERVICE="botocore"))
    def test_service_name_override(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        params = {
            "Bucket": "mybucket",
            "CreateBucketConfiguration": {
                "LocationConstraint": "us-west-2",
            },
        }
        s3.create_bucket(**params)
        params = dict(Key="foo", Bucket="mybucket", Body=b"bar")
        s3.put_object(**params)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == "botocore.s3", "Expected 'botocore.s3' but got {}".format(span.service)

        cfg = config.botocore
        cfg["service"] = "boto-service"

        s3.list_buckets()
        spans = self.get_spans()
        assert spans
        span = spans[-1]

        assert span.service == "boto-service.s3", "Expected 'boto-service.s3' but got {}".format(span.service)

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_s3_client_default(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == "aws.s3", "Expected 'aws.s3' but got {}".format(span.service)
        assert span.name == "s3.command"

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_s3_client_v0(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == "aws.s3", "Expected 'aws.s3' but got {}".format(span.service)
        assert span.name == "s3.command"

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_s3_client_v1(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == "mysvc", "Expected 'mysvc' but got {}".format(span.service)
        assert span.name == "aws.s3.request"

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_s3_client_default(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == "aws.s3"
        assert span.name == "s3.command"

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_s3_client_v0(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == "aws.s3"
        assert span.name == "s3.command"

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_s3_client_v1(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin.get_from(s3).clone(tracer=self.tracer).onto(s3)

        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME
        assert span.name == "aws.s3.request"

    def _test_sqs_client(self):
        self.sqs_client.delete_queue(QueueUrl=self.queue_name)  # Delete so we can test create_queue spans

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
        self.sqs_test_queue = self.sqs_client.create_queue(QueueName=self.queue_name)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "CreateQueue"
        assert span.get_tag("component") == "botocore"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.createqueue"
        return span

    @mock_sqs
    def test_sqs_client(self):
        span = self._test_sqs_client()
        assert span.get_tag("aws.sqs.queue_name") == "Test"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws_service") == "sqs"
        assert span.get_tag("queuename") == "Test"
        assert span.get_tag("component") == "botocore"

    @mock_sqs
    def test_sqs_client_no_params(self):
        with self.override_config("botocore", dict(tag_no_params=True)):
            span = self._test_sqs_client()
            assert span.get_tag("aws.sqs.queue_name") is None
            assert span.get_tag("queuename") is None
            assert span.get_tag("params.MessageBody") is None

    @mock_sqs
    def test_sqs_send_message_non_url_queue(self):
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)

        self.sqs_client.send_message(QueueUrl="Test", MessageBody="world")
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.operation") == "SendMessage"
        assert span.resource == "sqs.sendmessage"

    @mock_sqs
    def test_sqs_send_message_distributed_tracing_off(self):
        with self.override_config("botocore", dict(distributed_tracing=False)):
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)

            self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
            spans = self.get_spans()
            assert spans
            span = spans[0]
            assert len(spans) == 1
            assert span.get_tag("aws.region") == "us-east-1"
            assert span.get_tag("region") == "us-east-1"
            assert span.get_tag("aws.operation") == "SendMessage"
            assert span.get_tag("params.MessageBody") is None
            assert span.get_tag("component") == "botocore"
            assert span.get_tag("span.kind"), "client"
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            assert span.service == "test-botocore-tracing.sqs"
            assert span.resource == "sqs.sendmessage"
            assert span.get_tag("params.MessageAttributes._datadog.StringValue") is None
            response = self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )
            assert len(response["Messages"]) == 1
            trace_in_message = "MessageAttributes" in response["Messages"][0]
            assert trace_in_message is False

    @mock_sqs
    def test_sqs_send_message_distributed_tracing_on(self):
        with self.override_config("botocore", dict(distributed_tracing=True, propagation_enabled=True)):
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)

            self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
            spans = self.get_spans()
            assert spans
            produce_span = spans[0]
            assert len(spans) == 1
            assert produce_span.get_tag("aws.region") == "us-east-1"
            assert produce_span.get_tag("region") == "us-east-1"
            assert produce_span.get_tag("aws.operation") == "SendMessage"
            assert produce_span.get_tag("params.MessageBody") is None
            assert produce_span.get_tag("component") == "botocore"
            assert produce_span.get_tag("span.kind"), "client"
            assert_is_measured(produce_span)
            assert_span_http_status_code(produce_span, 200)
            assert produce_span.service == "test-botocore-tracing.sqs"
            assert produce_span.resource == "sqs.sendmessage"
            response = self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["All"],
                WaitTimeSeconds=5,
            )
            assert len(response["Messages"]) == 1
            trace_in_message = "MessageAttributes" in response["Messages"][0]
            assert trace_in_message is True

            spans = self.get_spans()
            assert spans
            consume_span = spans[1]
            assert len(spans) == 2

            assert consume_span.parent_id == produce_span.span_id
            assert consume_span.trace_id == produce_span.trace_id

    def test_distributed_tracing_sns_to_sqs_works(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        self._test_distributed_tracing_sns_to_sqs(False)

    @mock.patch.object(sys.modules["ddtrace.contrib.botocore.services.sqs"], "_encode_data")
    def test_distributed_tracing_sns_to_sqs_raw_delivery(self, mock_encode):
        """
        Moto doesn't currently handle raw delivery message handling quite correctly.
        In the real world, AWS will encode data for us. Moto does not.

        So, we patch our code here to encode the data
        """

        def _moto_compatible_encode(trace_data):
            return base64.b64encode(json.dumps(trace_data).encode("utf-8"))

        mock_encode.side_effect = _moto_compatible_encode
        self._test_distributed_tracing_sns_to_sqs(True)

    @mock_sns
    @mock_sqs
    def _test_distributed_tracing_sns_to_sqs(self, raw_message_delivery):
        with self.override_config("botocore", dict(distributed_tracing=True, propagation_enabled=True)):
            with mock.patch("time.time") as mt:
                mt.return_value = 1642544540

                sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")

                topic = sns.create_topic(Name="testTopic")

                topic_arn = topic["TopicArn"]
                sqs_url = self.sqs_test_queue["QueueUrl"]
                url_parts = sqs_url.split("/")
                sqs_arn = "arn:aws:sqs:{}:{}:{}".format("us-east-1", url_parts[-2], url_parts[-1])
                subscription = sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

                if raw_message_delivery:
                    sns.set_subscription_attributes(
                        SubscriptionArn=subscription["SubscriptionArn"],
                        AttributeName="RawMessageDelivery",
                        AttributeValue="true",
                    )

                Pin.get_from(sns).clone(tracer=self.tracer).onto(sns)
                Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

                sns.publish(TopicArn=topic_arn, Message="test")

                # get SNS messages via SQS
                response = self.sqs_client.receive_message(QueueUrl=self.sqs_test_queue["QueueUrl"], WaitTimeSeconds=2)

                # clean up resources
                sns.delete_topic(TopicArn=topic_arn)

                spans = self.get_spans()
                assert spans
                publish_span = spans[0]
                assert len(spans) == 3
                assert publish_span.get_tag("aws.region") == "us-east-1"
                assert publish_span.get_tag("region") == "us-east-1"
                assert publish_span.get_tag("aws.operation") == "Publish"
                assert publish_span.get_tag("params.MessageBody") is None
                assert publish_span.get_tag("component") == "botocore"
                assert publish_span.get_tag("span.kind"), "client"
                assert_is_measured(publish_span)
                assert_span_http_status_code(publish_span, 200)
                assert publish_span.service == "aws.sns"
                assert publish_span.resource == "sns.publish"

                assert len(response["Messages"]) == 1
                if raw_message_delivery:
                    trace_in_message = "MessageAttributes" in response["Messages"][0]
                else:
                    trace_in_message = "MessageAttributes" in response["Messages"][0]["Body"]
                assert trace_in_message is True

                consume_span = spans[1]

                assert consume_span.parent_id == publish_span.span_id
                assert consume_span.trace_id == publish_span.trace_id

    @mock_sqs
    def test_sqs_send_message_trace_injection_with_max_message_attributes(self):
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
        message_attributes = {
            "one": {"DataType": "String", "StringValue": "one"},
            "two": {"DataType": "String", "StringValue": "two"},
            "three": {"DataType": "String", "StringValue": "three"},
            "four": {"DataType": "String", "StringValue": "four"},
            "five": {"DataType": "String", "StringValue": "five"},
            "six": {"DataType": "String", "StringValue": "six"},
            "seven": {"DataType": "String", "StringValue": "seven"},
            "eight": {"DataType": "String", "StringValue": "eight"},
            "nine": {"DataType": "String", "StringValue": "nine"},
            "ten": {"DataType": "String", "StringValue": "ten"},
        }
        self.sqs_client.send_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes
        )
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessage"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessage"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        assert trace_json is None
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )
        assert len(response["Messages"]) == 1
        trace_in_message = "MessageAttributes" in response["Messages"][0]
        assert trace_in_message is False

    @mock_sqs
    def test_sqs_send_message_batch_trace_injection_with_no_message_attributes(self):
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
        entries = [
            {
                "Id": "1",
                "MessageBody": "ironmaiden",
            }
        ]
        self.sqs_client.send_message_batch(QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=entries)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessageBatch"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessagebatch"
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )
        assert len(response["Messages"]) == 1
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        assert get_128_bit_trace_id_from_headers(trace_data_in_message) == span.trace_id
        assert trace_data_in_message[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sqs
    def test_sqs_send_message_batch_trace_injection_with_message_attributes(self):
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
        entries = [
            {
                "Id": "1",
                "MessageBody": "ironmaiden",
                "MessageAttributes": {
                    "one": {"DataType": "String", "StringValue": "one"},
                    "two": {"DataType": "String", "StringValue": "two"},
                    "three": {"DataType": "String", "StringValue": "three"},
                    "four": {"DataType": "String", "StringValue": "four"},
                    "five": {"DataType": "String", "StringValue": "five"},
                    "six": {"DataType": "String", "StringValue": "six"},
                    "seven": {"DataType": "String", "StringValue": "seven"},
                    "eight": {"DataType": "String", "StringValue": "eight"},
                    "nine": {"DataType": "String", "StringValue": "nine"},
                },
            }
        ]

        self.sqs_client.send_message_batch(QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=entries)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessageBatch"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessagebatch"
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )
        assert len(response["Messages"]) == 1
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        assert get_128_bit_trace_id_from_headers(trace_data_in_message) == span.trace_id
        assert trace_data_in_message[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sqs
    def test_sqs_send_message_batch_trace_injection_with_max_message_attributes(self):
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
        entries = [
            {
                "Id": "1",
                "MessageBody": "ironmaiden",
                "MessageAttributes": {
                    "one": {"DataType": "String", "StringValue": "one"},
                    "two": {"DataType": "String", "StringValue": "two"},
                    "three": {"DataType": "String", "StringValue": "three"},
                    "four": {"DataType": "String", "StringValue": "four"},
                    "five": {"DataType": "String", "StringValue": "five"},
                    "six": {"DataType": "String", "StringValue": "six"},
                    "seven": {"DataType": "String", "StringValue": "seven"},
                    "eight": {"DataType": "String", "StringValue": "eight"},
                    "nine": {"DataType": "String", "StringValue": "nine"},
                    "ten": {"DataType": "String", "StringValue": "ten"},
                },
            }
        ]

        self.sqs_client.send_message_batch(QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=entries)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessageBatch"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessagebatch"
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )
        assert len(response["Messages"]) == 1
        trace_in_message = "MessageAttributes" in response["Messages"][0]
        assert trace_in_message is False

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_sqs_client_default(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)
        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        self.sqs_client.send_message_batch(
            QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=[{"Id": "1", "MessageBody": "hello"}]
        )
        self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        spans = self.get_spans()
        assert spans[0].service == "aws.sqs"
        assert spans[0].name == "sqs.command"
        assert spans[1].service == "aws.sqs"
        assert spans[1].name == "sqs.command"
        assert spans[2].service == "aws.sqs"
        assert spans[2].name == "sqs.command"

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_sqs_client_v0(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        self.sqs_client.send_message_batch(
            QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=[{"Id": "1", "MessageBody": "hello"}]
        )
        self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        spans = self.get_spans()
        assert spans[0].service == "aws.sqs"
        assert spans[0].name == "sqs.command"
        assert spans[1].service == "aws.sqs"
        assert spans[1].name == "sqs.command"
        assert spans[2].service == "aws.sqs"
        assert spans[2].name == "sqs.command"

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_sqs_client_v1(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        self.sqs_client.send_message_batch(
            QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=[{"Id": "1", "MessageBody": "hello"}]
        )
        self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        spans = self.get_spans()
        assert spans[0].service == "mysvc"
        assert spans[0].name == "aws.sqs.send"
        assert spans[1].service == "mysvc"
        assert spans[1].name == "aws.sqs.send"
        assert spans[2].service == "mysvc"
        assert spans[2].name == "aws.sqs.receive"

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_sqs_client_default(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        self.sqs_client.send_message_batch(
            QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=[{"Id": "1", "MessageBody": "hello"}]
        )
        self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        spans = self.get_spans()
        assert spans[0].service == "aws.sqs"
        assert spans[0].name == "sqs.command"
        assert spans[1].service == "aws.sqs"
        assert spans[1].name == "sqs.command"
        assert spans[2].service == "aws.sqs"
        assert spans[2].name == "sqs.command"

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_sqs_client_v0(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        self.sqs_client.send_message_batch(
            QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=[{"Id": "1", "MessageBody": "hello"}]
        )
        self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        spans = self.get_spans()
        assert spans[0].service == "aws.sqs"
        assert spans[0].name == "sqs.command"
        assert spans[1].service == "aws.sqs"
        assert spans[1].name == "sqs.command"
        assert spans[2].service == "aws.sqs"
        assert spans[2].name == "sqs.command"

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_sqs_client_v1(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        self.sqs_client.send_message_batch(
            QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=[{"Id": "1", "MessageBody": "hello"}]
        )
        self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        spans = self.get_spans()
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[1].name == "aws.sqs.send"
        assert spans[1].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[1].name == "aws.sqs.send"
        assert spans[2].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[2].name == "aws.sqs.receive"

    @mock_stepfunctions
    def test_stepfunctions_send_start_execution_trace_injection(self):
        sf = self.session.create_client("stepfunctions", region_name="us-west-2", endpoint_url="http://localhost:4566")
        sf.create_state_machine(
            name="lincoln",
            definition='{"StartAt": "HelloWorld","States": {"HelloWorld": {"Type": "Pass","End": true}}}',
            roleArn="arn:aws:iam::012345678901:role/DummyRole",
        )
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sf)
        sf.start_execution(
            stateMachineArn="arn:aws:states:us-west-2:000000000000:stateMachine:lincoln", input='{"baz":1}'
        )
        # I've tried to find a way to make Moto show me the input to the execution, but can't get that to work.
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.name == "states.command"  # This confirms our patch is working
        sf.delete_state_machine(stateMachineArn="arn:aws:states:us-west-2:000000000000:stateMachine:lincoln")

    @mock_stepfunctions
    def test_stepfunctions_send_start_execution_trace_injection_with_array_input(self):
        sf = self.session.create_client("stepfunctions", region_name="us-west-2", endpoint_url="http://localhost:4566")
        sf.create_state_machine(
            name="miller",
            definition='{"StartAt": "HelloWorld","States": {"HelloWorld": {"Type": "Pass","End": true}}}',
            roleArn="arn:aws:iam::012345678901:role/DummyRole",
        )
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sf)
        sf.start_execution(
            stateMachineArn="arn:aws:states:us-west-2:000000000000:stateMachine:miller", input='["one", "two", "three"]'
        )
        # I've tried to find a way to make Moto show me the input to the execution, but can't get that to work.
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.name == "states.command"  # This confirms our patch is working
        sf.delete_state_machine(stateMachineArn="arn:aws:states:us-west-2:000000000000:stateMachine:miller")

    @mock_stepfunctions
    def test_stepfunctions_send_start_execution_trace_injection_with_true_input(self):
        sf = self.session.create_client("stepfunctions", region_name="us-west-2", endpoint_url="http://localhost:4566")
        sf.create_state_machine(
            name="hobart",
            definition='{"StartAt": "HelloWorld","States": {"HelloWorld": {"Type": "Pass","End": true}}}',
            roleArn="arn:aws:iam::012345678901:role/DummyRole",
        )
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sf)
        sf.start_execution(stateMachineArn="arn:aws:states:us-west-2:000000000000:stateMachine:hobart", input="true")
        # I've tried to find a way to make Moto show me the input to the execution, but can't get that to work.
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.name == "states.command"  # This confirms our patch is working
        sf.delete_state_machine(stateMachineArn="arn:aws:states:us-west-2:000000000000:stateMachine:hobart")

    def _test_kinesis_client(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"
        client.create_stream(StreamName=stream_name, ShardCount=1)

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "PutRecords"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.putrecords"
        return span

    @mock_kinesis
    def test_kinesis_client_all_params_tagged(self):
        with self.override_config("botocore", dict(tag_no_params=False)):
            span = self._test_kinesis_client()
            assert span.get_tag("region") == "us-east-1"
            assert span.get_tag("aws_service") == "kinesis"
            assert span.get_tag("streamname") == "test"

    @mock_kinesis
    def test_kinesis_client(self):
        span = self._test_kinesis_client()
        assert span.get_tag("aws.kinesis.stream_name") == "test"
        assert span.get_tag("streamname") == "test"

    @mock_kinesis
    def test_kinesis_client_no_params(self):
        with self.override_config("botocore", dict(tag_no_params=True)):
            span = self._test_kinesis_client()
            assert span.get_tag("aws.kinesis.stream_name") is None
            assert span.get_tag("streamname") is None
            assert span.get_tag("params.Records") is None

    @mock_kinesis
    def test_kinesis_client_all_params(self):
        with self.override_config("botocore", dict(tag_no_params=True)):
            span = self._test_kinesis_client()
            assert span.get_tag("params.Records") is None
            assert span.get_tag("params.Data") is None
            assert span.get_tag("params.MessageBody") is None

    @mock_kinesis
    def test_kinesis_distributed_tracing_on(self):
        with self.override_config("botocore", dict(distributed_tracing=True, propagation_enabled=True)):
            # dict -> json string
            data = json.dumps({"json": "string"})

            self._test_kinesis_put_record_trace_injection("json_string", data)

            spans = self.get_spans()
            assert spans

            for span in spans:
                if span.get_tag("aws.operation") == "PutRecord":
                    produce_span = span
                elif span.get_tag("aws.operation") == "GetRecords":
                    consume_span = span

            assert consume_span.parent_id == produce_span.span_id
            assert consume_span.trace_id == produce_span.trace_id

    @mock_kinesis
    def test_unpatch(self):
        kinesis = self.session.create_client("kinesis", region_name="us-east-1")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(kinesis)

        unpatch()

        kinesis.list_streams()
        spans = self.get_spans()
        assert not spans, spans

    @mock_sqs
    def test_double_patch(self):
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)

        patch()
        patch()

        self.sqs_client.list_queues()

        spans = self.get_spans()
        assert spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sns_to_sqs(self):
        self._test_data_streams_sns_to_sqs(False)

    @mock.patch.object(sys.modules["ddtrace.contrib.botocore.services.sqs"], "_encode_data")
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sns_to_sqs_raw_delivery(self, mock_encode):
        """
        Moto doesn't currently handle raw delivery message handling quite correctly.
        In the real world, AWS will encode data for us. Moto does not.

        So, we patch our code here to encode the data
        """

        def _moto_compatible_encode(trace_data):
            return base64.b64encode(json.dumps(trace_data).encode("utf-8"))

        mock_encode.side_effect = _moto_compatible_encode
        self._test_data_streams_sns_to_sqs(True)

    @mock_sns
    @mock_sqs
    def _test_data_streams_sns_to_sqs(self, use_raw_delivery):
        # DEV: We want to mock time to ensure we only create a single bucket
        with mock.patch("time.time") as mt, mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            mt.return_value = 1642544540

            sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")

            topic = sns.create_topic(Name="testTopic")

            topic_arn = topic["TopicArn"]
            sqs_url = self.sqs_test_queue["QueueUrl"]
            url_parts = sqs_url.split("/")
            sqs_arn = "arn:aws:sqs:{}:{}:{}".format("us-east-1", url_parts[-2], url_parts[-1])
            subscription = sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

            if use_raw_delivery:
                sns.set_subscription_attributes(
                    SubscriptionArn=subscription["SubscriptionArn"],
                    AttributeName="RawMessageDelivery",
                    AttributeValue="true",
                )

            Pin.get_from(sns).clone(tracer=self.tracer).onto(sns)
            Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

            sns.publish(TopicArn=topic_arn, Message="test")

            self.get_spans()

            # get SNS messages via SQS
            self.sqs_client.receive_message(QueueUrl=self.sqs_test_queue["QueueUrl"], WaitTimeSeconds=2)

            # clean up resources
            sns.delete_topic(TopicArn=topic_arn)

            pin = Pin.get_from(sns)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1, "Expected 1 bucket but found {}".format(len(buckets))
            first = list(buckets.values())[0].pathway_stats

            assert (
                first[
                    (
                        "direction:out,topic:arn:aws:sns:us-east-1:000000000000:testTopic,type:sns",
                        3337976778666780987,
                        0,
                    )
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        "direction:out,topic:arn:aws:sns:us-east-1:000000000000:testTopic,type:sns",
                        3337976778666780987,
                        0,
                    )
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        "direction:out,topic:arn:aws:sns:us-east-1:000000000000:testTopic,type:sns",
                        3337976778666780987,
                        0,
                    )
                ].payload_size.count
                == 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 13854213076663332654, 3337976778666780987)
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 13854213076663332654, 3337976778666780987)
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 13854213076663332654, 3337976778666780987)
                ].payload_size.count
                == 1
            )

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        with mock.patch("time.time") as mt, mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            mt.return_value = 1642544540

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
            message_attributes = {
                "one": {"DataType": "String", "StringValue": "one"},
                "two": {"DataType": "String", "StringValue": "two"},
                "three": {"DataType": "String", "StringValue": "three"},
                "four": {"DataType": "String", "StringValue": "four"},
                "five": {"DataType": "String", "StringValue": "five"},
                "six": {"DataType": "String", "StringValue": "six"},
                "seven": {"DataType": "String", "StringValue": "seven"},
                "eight": {"DataType": "String", "StringValue": "eight"},
                "nine": {"DataType": "String", "StringValue": "nine"},
            }

            self.sqs_client.send_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes
            )

            self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            pin = Pin.get_from(self.sqs_client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].full_pathway_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].edge_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].payload_size.count == 1
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].payload_size.count
                == 1
            )

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs_batch(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        with mock.patch("time.time") as mt, mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            mt.return_value = 1642544540

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
            message_attributes = {
                "one": {"DataType": "String", "StringValue": "one"},
                "two": {"DataType": "String", "StringValue": "two"},
                "three": {"DataType": "String", "StringValue": "three"},
                "four": {"DataType": "String", "StringValue": "four"},
                "five": {"DataType": "String", "StringValue": "five"},
                "six": {"DataType": "String", "StringValue": "six"},
                "seven": {"DataType": "String", "StringValue": "seven"},
                "eight": {"DataType": "String", "StringValue": "eight"},
                "nine": {"DataType": "String", "StringValue": "nine"},
            }

            entries = [
                {"Id": "1", "MessageBody": "Message No. 1", "MessageAttributes": message_attributes},
                {"Id": "2", "MessageBody": "Message No. 2", "MessageAttributes": message_attributes},
                {"Id": "3", "MessageBody": "Message No. 3", "MessageAttributes": message_attributes},
            ]

            self.sqs_client.send_message_batch(QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=entries)

            self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MaxNumberOfMessages=3,
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            pin = Pin.get_from(self.sqs_client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].full_pathway_latency.count >= 3
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].edge_latency.count >= 3
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].payload_size.count == 3
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].full_pathway_latency.count
                >= 3
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].edge_latency.count
                >= 3
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].payload_size.count
                == 3
            )

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs_header_information(self):
        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=2,
            AttributeNames=[
                "All",
            ],
        )
        assert "_datadog" in response["Messages"][0]["MessageAttributes"]

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs_no_header(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        with mock.patch("time.time") as mt, mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            mt.return_value = 1642544540

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)
            message_attributes = {
                "one": {"DataType": "String", "StringValue": "one"},
                "two": {"DataType": "String", "StringValue": "two"},
                "three": {"DataType": "String", "StringValue": "three"},
                "four": {"DataType": "String", "StringValue": "four"},
                "five": {"DataType": "String", "StringValue": "five"},
                "six": {"DataType": "String", "StringValue": "six"},
                "seven": {"DataType": "String", "StringValue": "seven"},
                "eight": {"DataType": "String", "StringValue": "eight"},
                "nine": {"DataType": "String", "StringValue": "nine"},
                "ten": {"DataType": "String", "StringValue": "ten"},
            }

            self.sqs_client.send_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes
            )

            self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            pin = Pin.get_from(self.sqs_client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].full_pathway_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].edge_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].payload_size.count == 1
            assert first[("direction:in,topic:Test,type:sqs", 3569019635468821892, 0)].full_pathway_latency.count >= 1
            assert first[("direction:in,topic:Test,type:sqs", 3569019635468821892, 0)].edge_latency.count >= 1
            assert first[("direction:in,topic:Test,type:sqs", 3569019635468821892, 0)].payload_size.count == 1

    @mock_lambda
    def test_lambda_client(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-west-2"
        assert span.get_tag("region") == "us-west-2"
        assert span.get_tag("aws.operation") == "ListFunctions"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.lambda"
        assert span.resource == "lambda.listfunctions"
        assert span.get_tag("params.ClientContext") is None

    @mock_lambda
    def test_lambda_invoke_distributed_tracing_off(self):
        with self.override_config("botocore", dict(distributed_tracing=False)):
            lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
            lamb.create_function(
                FunctionName="ironmaiden",
                Runtime="python3.7",
                Role="test-iam-role",
                Handler="lambda_function.lambda_handler",
                Code={
                    "ZipFile": get_zip_lambda(),
                },
                Publish=True,
                Timeout=30,
                MemorySize=128,
            )

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(lamb)

            lamb.invoke(
                FunctionName="ironmaiden",
                Payload=json.dumps({}),
            )

            spans = self.get_spans()
            assert spans
            span = spans[0]

            assert len(spans) == 1
            assert span.get_tag("aws.region") == "us-west-2"
            assert span.get_tag("region") == "us-west-2"
            assert span.get_tag("aws.operation") == "Invoke"
            assert span.get_tag("component") == "botocore"
            assert span.get_tag("span.kind"), "client"
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            assert span.service == "test-botocore-tracing.lambda"
            assert span.resource == "lambda.invoke"
            assert span.get_tag("params.ClientContext") is None
            lamb.delete_function(FunctionName="ironmaiden")

    @mock_lambda
    def test_lambda_invoke_bad_context_client(self):
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        lamb.create_function(
            FunctionName="black-sabbath",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(lamb)

        lamb.invoke(
            FunctionName="black-sabbath",
            ClientContext="bad_client_context",
            Payload=json.dumps({}),
        )

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-west-2"
        assert span.get_tag("region") == "us-west-2"
        assert span.get_tag("aws.operation") == "Invoke"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        lamb.delete_function(FunctionName="black-sabbath")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_lambda_client_default(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")

        Pin.get_from(lamb).clone(tracer=self.tracer).onto(lamb)
        lamb.create_function(
            FunctionName="guns-and-roses",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )
        lamb.invoke(
            FunctionName="guns-and-roses",
            Payload=json.dumps({}),
        )
        lamb.delete_function(FunctionName="guns-and-roses")

        spans = self.get_spans()
        assert spans[0].service == "aws.lambda"
        assert spans[0].name == "lambda.command"
        assert spans[1].service == "aws.lambda"
        assert spans[1].name == "lambda.command"

    @mock_lambda
    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", endpoint_url="http://localhost:4566"
        )
    )
    def test_schematized_lambda_client_v0(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        Pin.get_from(lamb).clone(tracer=self.tracer).onto(lamb)

        lamb.create_function(
            FunctionName="guns-and-roses",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )
        lamb.invoke(
            FunctionName="guns-and-roses",
            Payload=json.dumps({}),
        )
        lamb.delete_function(FunctionName="guns-and-roses")

        spans = self.get_spans()
        assert spans[0].service == "aws.lambda"
        assert spans[0].name == "lambda.command"
        assert spans[1].service == "aws.lambda"
        assert spans[1].name == "lambda.command"

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_lambda_client_v1(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        Pin.get_from(lamb).clone(tracer=self.tracer).onto(lamb)

        lamb.create_function(
            FunctionName="guns-and-roses",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )
        lamb.invoke(
            FunctionName="guns-and-roses",
            Payload=json.dumps({}),
        )
        lamb.delete_function(FunctionName="guns-and-roses")

        spans = self.get_spans()
        assert spans[0].service == "mysvc"
        assert spans[0].name == "aws.lambda.request"
        assert spans[1].service == "mysvc"
        assert spans[1].name == "aws.lambda.invoke"

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_lambda_client_default(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        Pin.get_from(lamb).clone(tracer=self.tracer).onto(lamb)

        lamb.create_function(
            FunctionName="guns-and-roses",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )
        lamb.invoke(
            FunctionName="guns-and-roses",
            Payload=json.dumps({}),
        )
        lamb.delete_function(FunctionName="guns-and-roses")

        spans = self.get_spans()
        assert spans[0].service == "aws.lambda"
        assert spans[0].name == "lambda.command"
        assert spans[1].service == "aws.lambda"
        assert spans[1].name == "lambda.command"

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_lambda_client_v0(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        Pin.get_from(lamb).clone(tracer=self.tracer).onto(lamb)

        lamb.create_function(
            FunctionName="guns-and-roses",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )
        lamb.invoke(
            FunctionName="guns-and-roses",
            Payload=json.dumps({}),
        )
        lamb.delete_function(FunctionName="guns-and-roses")

        spans = self.get_spans()
        assert spans[0].service == "aws.lambda"
        assert spans[0].name == "lambda.command"
        assert spans[1].service == "aws.lambda"
        assert spans[1].name == "lambda.command"

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_lambda_client_v1(self):
        # DEV: No lambda params tagged so we only check no ClientContext
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        Pin.get_from(lamb).clone(tracer=self.tracer).onto(lamb)

        lamb.create_function(
            FunctionName="guns-and-roses",
            Runtime="python3.7",
            Role="test-iam-role",
            Handler="lambda_function.lambda_handler",
            Code={
                "ZipFile": get_zip_lambda(),
            },
            Publish=True,
            Timeout=30,
            MemorySize=128,
        )
        lamb.invoke(
            FunctionName="guns-and-roses",
            Payload=json.dumps({}),
        )
        lamb.delete_function(FunctionName="guns-and-roses")

        spans = self.get_spans()
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[0].name == "aws.lambda.request"
        assert spans[1].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[1].name == "aws.lambda.invoke"

    @mock_events
    def test_eventbridge_single_entry_trace_injection(self):
        bridge = self.session.create_client("events", region_name="us-east-1", endpoint_url="http://localhost:4566")
        bridge.create_event_bus(Name="a-test-bus")

        entries = [
            {
                "Source": "some-event-source",
                "DetailType": "some-event-detail-type",
                "Detail": json.dumps({"foo": "bar"}),
                "EventBusName": "a-test-bus",
            }
        ]
        bridge.put_rule(
            Name="a-test-bus-rule",
            EventBusName="a-test-bus",
            EventPattern="""{"source": [{"prefix": ""}]}""",
            State="ENABLED",
        )

        bridge.list_rules()
        queue_url = self.sqs_test_queue["QueueUrl"]
        bridge.put_targets(
            Rule="a-test-bus-rule",
            Targets=[{"Id": "a-test-bus-rule-target", "Arn": "arn:aws:sqs:us-east-1:000000000000:Test"}],
        )

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(bridge)
        bridge.put_events(Entries=entries)

        messages = self.sqs_client.receive_message(QueueUrl=queue_url, WaitTimeSeconds=2)

        bridge.delete_event_bus(Name="a-test-bus")

        spans = self.get_spans()
        assert spans
        assert len(spans) == 2
        span = spans[0]
        str_entries = span.get_tag("params.Entries")
        put_rule_span = spans[1]
        assert put_rule_span.get_tag("rulename") == "a-test-bus"
        assert put_rule_span.get_tag("aws_service") == "events"
        assert put_rule_span.get_tag("region") == "us-east-1"
        assert str_entries is None

        message = messages["Messages"][0]
        body = message.get("Body")
        assert body is not None
        # body_obj = ast.literal_eval(body)
        body_obj = json.loads(body)
        detail = body_obj.get("detail")
        headers = detail.get("_datadog")
        assert headers is not None
        assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_events
    def test_eventbridge_multiple_entries_trace_injection(self):
        bridge = self.session.create_client("events", region_name="us-east-1", endpoint_url="http://localhost:4566")
        bridge.create_event_bus(Name="a-test-bus")

        entries = [
            {
                "Source": "another-event-source",
                "DetailType": "a-different-event-detail-type",
                "Detail": json.dumps({"abc": "xyz"}),
                "EventBusName": "a-test-bus",
            },
            {
                "Source": "some-event-source",
                "DetailType": "some-event-detail-type",
                "Detail": json.dumps({"foo": "bar"}),
                "EventBusName": "a-test-bus",
            },
        ]
        bridge.put_rule(
            Name="a-test-bus-rule",
            EventBusName="a-test-bus",
            EventPattern="""{"source": [{"prefix": ""}]}""",
            State="ENABLED",
        )

        bridge.list_rules()
        queue_url = self.sqs_test_queue["QueueUrl"]
        bridge.put_targets(
            Rule="a-test-bus-rule",
            Targets=[{"Id": "a-test-bus-rule-target", "Arn": "arn:aws:sqs:us-east-1:000000000000:Test"}],
        )

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(bridge)
        bridge.put_events(Entries=entries)

        messages = self.sqs_client.receive_message(QueueUrl=queue_url, WaitTimeSeconds=2)

        bridge.delete_event_bus(Name="a-test-bus")

        spans = self.get_spans()
        assert spans
        assert len(spans) == 2
        span = spans[0]
        str_entries = span.get_tag("params.Entries")
        assert str_entries is None

        message = messages["Messages"][0]
        body = message.get("Body")
        assert body is not None
        body_obj = json.loads(body)
        detail = body_obj.get("detail")
        headers = detail.get("_datadog")
        assert headers is not None
        assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

        # the following doesn't work due to an issue in moto/localstack where
        # an SQS message is generated per put_events rather than per event sent

        # message = messages["Messages"][1]
        # body = message.get("Body")
        # assert body is not None
        # body_obj = json.loads(body)
        # detail = body_obj.get("detail")
        # headers = detail.get("_datadog")
        # assert headers is not None
        # assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
        # assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_kms
    def test_kms_client(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "ListKeys"
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kms"
        assert span.resource == "kms.listkeys"

        # checking for protection on sts against security leak
        assert span.get_tag("params") is None

    @mock_kms
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_kms_client_default(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin.get_from(kms).clone(tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.service == "aws.kms"
        assert span.name == "kms.command"

    @mock_kms
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_kms_client_v0(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin.get_from(kms).clone(tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.service == "aws.kms"
        assert span.name == "kms.command"

    @mock_kms
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_kms_client_v1(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin.get_from(kms).clone(tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.service == "mysvc"
        assert span.name == "aws.kms.request"

    @mock_kms
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_kms_client_default(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin.get_from(kms).clone(tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.service == "aws.kms"
        assert span.name == "kms.command"

    @mock_kms
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_kms_client_v0(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin.get_from(kms).clone(tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.service == "aws.kms"
        assert span.name == "kms.command"

    @mock_kms
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_kms_client_v1(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin.get_from(kms).clone(tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.service == DEFAULT_SPAN_SERVICE_NAME
        assert span.name == "aws.kms.request"

    @mock_ec2
    def test_traced_client_ot(self):
        """OpenTracing version of test_traced_client."""
        ot_tracer = init_tracer("ec2_svc", self.tracer)

        with ot_tracer.start_active_span("ec2_op"):
            ec2 = self.session.create_client("ec2", region_name="us-west-2")
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(ec2)
            ec2.describe_instances()

        spans = self.get_spans()
        assert spans
        assert len(spans) == 2

        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "ec2_op"
        assert ot_span.service == "ec2_svc"

        assert dd_span.get_tag("aws.agent") == "botocore"
        assert dd_span.get_tag("aws.region") == "us-west-2"
        assert dd_span.get_tag("region") == "us-west-2"
        assert dd_span.get_tag("aws.operation") == "DescribeInstances"
        assert dd_span.get_tag("component") == "botocore"
        assert dd_span.get_tag("span.kind"), "client"
        assert_span_http_status_code(dd_span, 200)
        assert dd_span.get_metric("retry_attempts") == 0
        assert dd_span.service == "test-botocore-tracing.ec2"
        assert dd_span.resource == "ec2.describeinstances"
        assert dd_span.name == "ec2.command"

    @unittest.skipIf(BOTOCORE_VERSION < (1, 9, 0), "Skipping for older versions of botocore without Stubber")
    def test_stubber_no_response_metadata(self):
        """When no ResponseMetadata key is provided in the response"""
        from botocore.stub import Stubber

        response = {
            "Owner": {"ID": "foo", "DisplayName": "bar"},
            "Buckets": [{"CreationDate": datetime.datetime(2016, 1, 20, 22, 9), "Name": "baz"}],
        }

        s3 = self.session.create_client("s3", aws_access_key_id="foo", aws_secret_access_key="bar")
        with Stubber(s3) as stubber:
            stubber.add_response("list_buckets", response, {})
            service_response = s3.list_buckets()
            assert service_response == response

    @mock_firehose
    def test_firehose_no_records_arg(self):
        firehose = self.session.create_client("firehose", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(firehose)

        stream_name = "test-stream"
        account_id = "test-account"

        firehose.create_delivery_stream(
            DeliveryStreamName=stream_name,
            RedshiftDestinationConfiguration={
                "RoleARN": "arn:aws:iam::{}:role/firehose_delivery_role".format(account_id),
                "ClusterJDBCURL": "jdbc:redshift://host.amazonaws.com:5439/database",
                "CopyCommand": {
                    "DataTableName": "outputTable",
                    "CopyOptions": "CSV DELIMITER ',' NULL '\\0'",
                },
                "Username": "username",
                "Password": "password",
                "S3Configuration": {
                    "RoleARN": "arn:aws:iam::{}:role/firehose_delivery_role".format(account_id),
                    "BucketARN": "arn:aws:s3:::kinesis-test",
                    "Prefix": "myFolder/",
                    "BufferingHints": {"SizeInMBs": 123, "IntervalInSeconds": 124},
                    "CompressionFormat": "UNCOMPRESSED",
                },
            },
        )

        firehose.put_record_batch(
            DeliveryStreamName=stream_name,
            Records=[{"Data": "some data"}],
        )

        spans = self.get_spans()

        assert spans
        assert len(spans) == 2
        assert all(span.name == "firehose.command" for span in spans)

        delivery_stream_span, put_record_batch_span = spans
        assert delivery_stream_span.get_tag("aws.operation") == "CreateDeliveryStream"
        assert put_record_batch_span.get_tag("aws.operation") == "PutRecordBatch"
        assert put_record_batch_span.get_tag("params.Records") is None

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_DISTRIBUTED_TRACING="true"))
    def test_distributed_tracing_env_override(self):
        assert config.botocore.distributed_tracing is True

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_DISTRIBUTED_TRACING="false"))
    def test_distributed_tracing_env_override_false(self):
        assert config.botocore.distributed_tracing is False

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_PROPAGATION_ENABLED="true"))
    def test_propagation_enabled_env_override(self):
        assert config.botocore.propagation_enabled is True

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_PROPAGATION_ENABLED="false"))
    def test_propagation_enabled_env_override_false(self):
        assert config.botocore.propagation_enabled is False

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT="true"))
    def test_invoke_legacy_context_env_override(self):
        assert config.botocore.invoke_with_legacy_context is True

    def _test_sns(self, use_default_tracer=False):
        sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")

        topic_arn = topic["TopicArn"]
        sqs_url = self.sqs_test_queue["QueueUrl"]
        url_parts = sqs_url.split("/")
        sqs_arn = "arn:aws:sqs:{}:{}:{}".format("us-east-1", url_parts[-2], url_parts[-1])
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

        if use_default_tracer:
            Pin.get_from(sns).clone(tracer=self.tracer).onto(sns)
        else:
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        sns.publish(TopicArn=topic_arn, Message="test")
        spans = self.get_spans()

        # get SNS messages via SQS
        _ = self.sqs_client.receive_message(QueueUrl=self.sqs_test_queue["QueueUrl"], WaitTimeSeconds=2)

        # clean up resources
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert len(spans) == 2, "Expected 2 spans, found {}".format(len(spans))
        return spans[0]

    @mock_sns
    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_sns_client_default(self):
        span = self._test_sns(use_default_tracer=True)
        assert span.service == "aws.sns"
        assert span.name == "sns.command"

    @mock_sns
    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_sns_client_v0(self):
        span = self._test_sns(use_default_tracer=True)
        assert span.service == "aws.sns"
        assert span.name == "sns.command"

    @mock_sns
    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_sns_client_v1(self):
        span = self._test_sns(use_default_tracer=True)
        assert span.service == "mysvc"
        assert span.name == "aws.sns.send", span.name

    @mock_sns
    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_sns_client_default(self):
        span = self._test_sns(use_default_tracer=True)
        assert span.service == "aws.sns"
        assert span.name == "sns.command"

    @mock_sns
    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_sns_client_v0(self):
        span = self._test_sns(use_default_tracer=True)
        assert span.service == "aws.sns"
        assert span.name == "sns.command"

    @mock_sns
    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_sns_client_v1(self):
        span = self._test_sns(use_default_tracer=True)
        assert span.service == DEFAULT_SPAN_SERVICE_NAME
        assert span.name == "aws.sns.send"

    @mock_sns
    @mock_sqs
    def test_sns_all_params_tagged(self):
        with self.override_config("botocore", dict(tag_no_params=False)):
            span = self._test_sns()
            assert span.get_tag("region") == "us-east-1"
            assert span.get_tag("topicname") == "testTopic"
            assert span.get_tag("aws_service") == "sns"

    @mock_sns
    @mock_sqs
    def test_sns(self):
        span = self._test_sns()
        assert span.get_tag("aws.sns.topic_arn") == "arn:aws:sns:us-east-1:000000000000:testTopic"

    @mock_sns
    @mock_sqs
    def test_sns_no_params(self):
        with self.override_config("botocore", dict(tag_no_params=True)):
            span = self._test_sns()
            assert span.get_tag("aws.sns.topic_arn") is None

    @mock_sns
    @mock_sqs
    def test_sns_send_message_trace_injection_with_no_message_attributes(self):
        # TODO: Move away from inspecting MessageAttributes using span tag
        sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")

        topic_arn = topic["TopicArn"]
        sqs_url = self.sqs_test_queue["QueueUrl"]
        url_parts = sqs_url.split("/")
        sqs_arn = "arn:aws:sqs:{}:{}:{}".format("us-east-1", url_parts[-2], url_parts[-1])
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        sns.publish(TopicArn=topic_arn, Message="test")
        spans = self.get_spans()

        # get SNS messages via SQS
        response = self.sqs_client.receive_message(QueueUrl=self.sqs_test_queue["QueueUrl"], WaitTimeSeconds=2)

        # clean up resources
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "Publish"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sns"
        assert span.resource == "sns.publish"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        assert trace_json is None

        # receive message using SQS and ensure headers are present
        assert len(response["Messages"]) == 1
        msg = response["Messages"][0]
        assert msg is not None
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        assert msg_str == "test"
        msg_attr = msg_body["MessageAttributes"]
        assert msg_attr.get("_datadog") is not None
        assert msg_attr["_datadog"]["Type"] == "Binary"
        datadog_value_decoded = base64.b64decode(msg_attr["_datadog"]["Value"])
        headers = json.loads(datadog_value_decoded.decode())
        assert headers is not None
        assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sns
    @mock_sqs
    def test_sns_send_message_trace_injection_with_message_attributes(self):
        # TODO: Move away from inspecting MessageAttributes using span tag
        region = "us-east-1"
        sns = self.session.create_client("sns", region_name=region, endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")

        topic_arn = topic["TopicArn"]
        sqs_url = self.sqs_test_queue["QueueUrl"]
        url_parts = sqs_url.split("/")
        sqs_arn = "arn:aws:sqs:{}:{}:{}".format(region, url_parts[-2], url_parts[-1])
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        message_attributes = {
            "one": {"DataType": "String", "StringValue": "one"},
            "two": {"DataType": "String", "StringValue": "two"},
            "three": {"DataType": "String", "StringValue": "three"},
            "four": {"DataType": "String", "StringValue": "four"},
            "five": {"DataType": "String", "StringValue": "five"},
            "six": {"DataType": "String", "StringValue": "six"},
            "seven": {"DataType": "String", "StringValue": "seven"},
            "eight": {"DataType": "String", "StringValue": "eight"},
            "nine": {"DataType": "String", "StringValue": "nine"},
        }

        sns.publish(TopicArn=topic_arn, Message="test", MessageAttributes=message_attributes)
        spans = self.get_spans()

        # get SNS messages via SQS
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        # clean up resources
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "Publish"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sns"
        assert span.resource == "sns.publish"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        assert trace_json is None

        # receive message using SQS and ensure headers are present
        assert len(response["Messages"]) == 1
        msg = response["Messages"][0]
        assert msg is not None
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        assert msg_str == "test"
        msg_attr = msg_body["MessageAttributes"]
        assert msg_attr.get("_datadog") is not None
        assert msg_attr["_datadog"]["Type"] == "Binary"
        datadog_value_decoded = base64.b64decode(msg_attr["_datadog"]["Value"])
        headers = json.loads(datadog_value_decoded.decode())
        assert headers is not None
        assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sns
    @mock_sqs
    def test_sns_send_message_trace_injection_with_max_message_attributes(self):
        # TODO: Move away from inspecting MessageAttributes using span tag
        region = "us-east-1"
        sns = self.session.create_client("sns", region_name=region, endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")

        topic_arn = topic["TopicArn"]
        sqs_url = self.sqs_test_queue["QueueUrl"]
        url_parts = sqs_url.split("/")
        sqs_arn = "arn:aws:sqs:{}:{}:{}".format(region, url_parts[-2], url_parts[-1])
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        message_attributes = {
            "one": {"DataType": "String", "StringValue": "one"},
            "two": {"DataType": "String", "StringValue": "two"},
            "three": {"DataType": "String", "StringValue": "three"},
            "four": {"DataType": "String", "StringValue": "four"},
            "five": {"DataType": "String", "StringValue": "five"},
            "six": {"DataType": "String", "StringValue": "six"},
            "seven": {"DataType": "String", "StringValue": "seven"},
            "eight": {"DataType": "String", "StringValue": "eight"},
            "nine": {"DataType": "String", "StringValue": "nine"},
            "ten": {"DataType": "String", "StringValue": "ten"},
        }

        sns.publish(TopicArn=topic_arn, Message="test", MessageAttributes=message_attributes)
        spans = self.get_spans()

        # get SNS messages via SQS
        response = self.sqs_client.receive_message(QueueUrl=self.sqs_test_queue["QueueUrl"], WaitTimeSeconds=2)

        # clean up resources
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.operation") == "Publish"
        assert span.get_tag("params.MessageBody") is None
        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sns"
        assert span.resource == "sns.publish"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        assert trace_json is None

        # receive message using SQS and ensure headers are present
        assert len(response["Messages"]) == 1
        msg = response["Messages"][0]
        assert msg is not None
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        assert msg_str == "test"
        msg_attr = msg_body["MessageAttributes"]
        assert msg_attr.get("_datadog") is None

    @mock_sns
    @mock_sqs
    def test_sns_send_message_batch_trace_injection_with_no_message_attributes(self):
        with self.override_config("botocore", dict(distributed_tracing=True, propagation_enabled=True)):
            region = "us-east-1"
            sns = self.session.create_client("sns", region_name=region, endpoint_url="http://localhost:4566")

            topic = sns.create_topic(Name="testTopic")

            topic_arn = topic["TopicArn"]
            sqs_url = self.sqs_test_queue["QueueUrl"]
            url_parts = sqs_url.split("/")
            sqs_arn = "arn:aws:sqs:{}:{}:{}".format(region, url_parts[-2], url_parts[-1])
            sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)
            Pin.get_from(sns).clone(tracer=self.tracer).onto(self.sqs_client)
            entries = [
                {
                    "Id": "1",
                    "Message": "ironmaiden",
                },
                {
                    "Id": "2",
                    "Message": "megadeth",
                },
            ]

            sns.publish_batch(TopicArn=topic_arn, PublishBatchRequestEntries=entries)

            # get SNS messages via SQS
            response = self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
                MaxNumberOfMessages=2,
            )

            spans = self.get_spans()

            # check if the appropriate span was generated
            assert spans
            span = spans[0]
            assert len(spans) == 2
            assert span.get_tag("aws.region") == region
            assert span.get_tag("region") == region
            assert span.get_tag("aws.operation") == "PublishBatch"
            assert span.get_tag("params.MessageBody") is None
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            assert span.service == "test-botocore-tracing.sns"
            assert span.resource == "sns.publishbatch"

            # receive messages using SQS and ensure headers are present
            assert len(response["Messages"]) == 2
            msg_1 = response["Messages"][0]
            assert msg_1 is not None

            msg_body = json.loads(msg_1["Body"])
            msg_str = msg_body["Message"]
            assert msg_str == "ironmaiden"
            msg_attr = msg_body["MessageAttributes"]
            assert msg_attr.get("_datadog") is not None
            headers = json.loads(base64.b64decode(msg_attr["_datadog"]["Value"]))
            assert headers is not None
            assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
            assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

            msg_2 = response["Messages"][1]
            assert msg_2 is not None

            msg_body = json.loads(msg_2["Body"])
            msg_str = msg_body["Message"]
            assert msg_str == "megadeth"
            msg_attr = msg_body["MessageAttributes"]
            assert msg_attr.get("_datadog") is not None
            headers = json.loads(base64.b64decode(msg_attr["_datadog"]["Value"]))
            assert headers is not None
            assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
            assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

            consume_span = spans[1]

            assert consume_span.get_tag("aws.operation") == "ReceiveMessage"

            assert consume_span.parent_id == span.span_id
            assert consume_span.trace_id == span.trace_id

            # clean up resources
            sns.delete_topic(TopicArn=topic_arn)

    @pytest.mark.skipif(
        PYTHON_VERSION_INFO < (3, 6),
        reason="Skipping for older py versions whose latest supported boto versions don't have sns.publish_batch",
    )
    @mock_sns
    @mock_sqs
    def test_sns_send_message_batch_trace_injection_with_message_attributes(self):
        region = "us-east-1"
        sns = self.session.create_client("sns", region_name=region, endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")

        topic_arn = topic["TopicArn"]
        sqs_url = self.sqs_test_queue["QueueUrl"]
        url_parts = sqs_url.split("/")
        sqs_arn = "arn:aws:sqs:{}:{}:{}".format(region, url_parts[-2], url_parts[-1])
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        message_attributes = {
            "one": {"DataType": "String", "StringValue": "one"},
            "two": {"DataType": "String", "StringValue": "two"},
            "three": {"DataType": "String", "StringValue": "three"},
            "four": {"DataType": "String", "StringValue": "four"},
            "five": {"DataType": "String", "StringValue": "five"},
            "six": {"DataType": "String", "StringValue": "six"},
            "seven": {"DataType": "String", "StringValue": "seven"},
            "eight": {"DataType": "String", "StringValue": "eight"},
            "nine": {"DataType": "String", "StringValue": "nine"},
        }
        entries = [
            {"Id": "1", "Message": "ironmaiden", "MessageAttributes": message_attributes},
            {"Id": "2", "Message": "megadeth", "MessageAttributes": message_attributes},
        ]
        sns.publish_batch(TopicArn=topic_arn, PublishBatchRequestEntries=entries)
        spans = self.get_spans()

        # get SNS messages via SQS
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        # clean up resources
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == region
        assert span.get_tag("region") == region
        assert span.get_tag("aws.operation") == "PublishBatch"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sns"
        assert span.resource == "sns.publishbatch"

        # receive message using SQS and ensure headers are present
        assert len(response["Messages"]) == 1
        msg = response["Messages"][0]
        assert msg is not None
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        assert msg_str == "ironmaiden"
        msg_attr = msg_body["MessageAttributes"]
        assert msg_attr.get("_datadog") is not None
        headers = json.loads(base64.b64decode(msg_attr["_datadog"]["Value"]))
        assert headers is not None
        assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sns
    @mock_sqs
    def test_sns_send_message_batch_trace_injection_with_max_message_attributes(self):
        region = "us-east-1"
        sns = self.session.create_client("sns", region_name=region, endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")

        topic_arn = topic["TopicArn"]
        sqs_url = self.sqs_test_queue["QueueUrl"]
        url_parts = sqs_url.split("/")
        sqs_arn = "arn:aws:sqs:{}:{}:{}".format(region, url_parts[-2], url_parts[-1])
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        message_attributes = {
            "one": {"DataType": "String", "StringValue": "one"},
            "two": {"DataType": "String", "StringValue": "two"},
            "three": {"DataType": "String", "StringValue": "three"},
            "four": {"DataType": "String", "StringValue": "four"},
            "five": {"DataType": "String", "StringValue": "five"},
            "six": {"DataType": "String", "StringValue": "six"},
            "seven": {"DataType": "String", "StringValue": "seven"},
            "eight": {"DataType": "String", "StringValue": "eight"},
            "nine": {"DataType": "String", "StringValue": "nine"},
            "ten": {"DataType": "String", "StringValue": "ten"},
        }
        entries = [
            {"Id": "1", "Message": "ironmaiden", "MessageAttributes": message_attributes},
            {"Id": "2", "Message": "megadeth", "MessageAttributes": message_attributes},
        ]
        sns.publish_batch(TopicArn=topic_arn, PublishBatchRequestEntries=entries)
        spans = self.get_spans()

        # get SNS messages via SQS
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MessageAttributeNames=["_datadog"],
            WaitTimeSeconds=2,
        )

        # clean up resources
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == region
        assert span.get_tag("region") == region
        assert span.get_tag("aws.operation") == "PublishBatch"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sns"
        assert span.resource == "sns.publishbatch"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        assert trace_json is None

        # receive message using SQS and ensure headers are present
        assert response.get("Messages"), response
        assert len(response["Messages"]) == 1
        msg = response["Messages"][0]
        assert msg is not None
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        assert msg_str == "ironmaiden"
        msg_attr = msg_body["MessageAttributes"]
        assert msg_attr.get("_datadog") is None

    def _kinesis_get_shard_iterator(self, client, stream_name, shard_id):
        response = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = response["ShardIterator"]

        return shard_iterator

    def _kinesis_create_stream(self, client, stream_name):
        client.create_stream(StreamName=stream_name, ShardCount=1)
        stream = client.describe_stream(StreamName=stream_name)["StreamDescription"]
        shard_id = stream["Shards"][0]["ShardId"]

        return shard_id, stream["StreamARN"]

    def _kinesis_get_records(self, client, shard_iterator, stream_arn, enable_stream_arn=False):
        response = None
        if enable_stream_arn:
            response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)
        else:
            response = client.get_records(ShardIterator=shard_iterator)
        records = response["Records"]

        return records

    def _kinesis_assert_spans(self):
        spans = self.get_spans()
        assert spans
        assert len(spans) == 1

        span = spans[0]
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("params.MessageBody") is None

        assert span.get_tag("component") == "botocore"
        assert span.get_tag("span.kind"), "client"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"

        return span

    def _kinesis_assert_records(self, records, span):
        record = records[0]
        record_data = record["Data"]
        assert record_data is not None

        decoded_record_data = {}
        try:
            decoded_record_data = record_data.decode("ascii")
            decoded_record_data_json = json.loads(decoded_record_data)
            headers = decoded_record_data_json["_datadog"]
            assert headers is not None
            assert get_128_bit_trace_id_from_headers(headers) == span.trace_id
            assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)
        except Exception:
            # injection was not successful, so record should be exceeding 1MB in size
            decoded_record_data = json.loads(base64.b64decode(record_data).decode("ascii"))
            assert "_datadog" not in decoded_record_data

        return decoded_record_data

    @mock_kinesis
    def test_kinesis_get_records_empty_poll_disabled(self):
        # Tests that no span is created when empty poll is disabled and we received no records.
        with self.override_config("botocore", dict(empty_poll_enabled=False)):
            client = self.session.create_client("kinesis", region_name="us-east-1")

            stream_name = "kinesis_get_records_empty_poll_disabled"
            shard_id, _ = self._kinesis_create_stream(client, stream_name)

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)

            shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

            # pop any spans created from previous operations
            spans = self.pop_spans()

            response = None
            response = client.get_records(ShardIterator=shard_iterator)
            records = response["Records"]

            assert len(records) == 0

            spans = self.get_spans()
            assert len(spans) == 0

    @mock_kinesis
    def test_kinesis_get_records_empty_poll_enabled(self):
        # Tests that a span is created when empty poll is enabled and we received no records.
        with self.override_config("botocore", dict(empty_poll_enabled=True)):
            client = self.session.create_client("kinesis", region_name="us-east-1")

            stream_name = "kinesis_get_records_empty_poll_enabled"
            shard_id, _ = self._kinesis_create_stream(client, stream_name)

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)

            shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

            # pop any spans created from previous operations
            spans = self.pop_spans()

            response = None
            response = client.get_records(ShardIterator=shard_iterator)
            records = response["Records"]

            assert len(records) == 0

            spans = self.get_spans()
            assert len(spans) == 1

    @mock_sqs
    def test_sqs_get_records_empty_poll_disabled(self):
        # Tests that no span is created when empty poll is disabled and we received no records.
        with self.override_config("botocore", dict(empty_poll_enabled=False)):
            # pop any spans created from previous operations
            spans = self.pop_spans()

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)

            response = None
            response = self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            assert "Messages" not in response

            spans = self.get_spans()
            assert len(spans) == 0

    @mock_sqs
    def test_sqs_get_records_empty_poll_enabled(self):
        # Tests that a span is created when empty poll is enabled and we received no records.
        with self.override_config("botocore", dict(empty_poll_enabled=True)):
            # pop any spans created from previous operations
            spans = self.pop_spans()

            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.sqs_client)

            response = None
            response = self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            assert "Messages" not in response

            spans = self.get_spans()
            assert len(spans) == 1

    def _test_kinesis_put_record_trace_injection(self, test_name, data, client=None, enable_stream_arn=False):
        if not client:
            client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_record_" + test_name
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        partition_key = "1234"

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        if enable_stream_arn:
            client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key, StreamARN=stream_arn)
        else:
            client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key)

        # assert commons for span
        span = self._kinesis_assert_spans()

        # assert operation specifics for span
        assert span.get_tag("aws.operation") == "PutRecord"
        assert span.resource == "kinesis.putrecord"

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)
        records = self._kinesis_get_records(client, shard_iterator, stream_arn, enable_stream_arn=enable_stream_arn)

        # assert commons for records
        decoded_record_data = self._kinesis_assert_records(records, span)

        # assert operation specifics for records
        assert len(records) == 1

        client.delete_stream(StreamName=stream_name)

        return decoded_record_data

    def _test_kinesis_put_records_trace_injection(self, test_name, data, client=None, enable_stream_arn=False):
        if not client:
            client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_" + test_name
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        if enable_stream_arn:
            client.put_records(StreamName=stream_name, Records=data, StreamARN=stream_arn)
        else:
            client.put_records(StreamName=stream_name, Records=data)

        # assert commons for span
        span = self._kinesis_assert_spans()

        # assert operation specifics for span
        assert span.get_tag("aws.operation") == "PutRecords"
        assert span.resource == "kinesis.putrecords"

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)
        records = self._kinesis_get_records(client, shard_iterator, stream_arn, enable_stream_arn=enable_stream_arn)

        # assert commons for records
        decoded_record_data = self._kinesis_assert_records(records, span)

        # assert operation specifics for records
        assert len(records) == len(data)

        # assert operation specifics for records
        # make sure there's no trace context in the next record
        record = records[1]

        next_decoded_record = {}
        try:
            next_decoded_record = json.loads(record["Data"].decode("ascii"))
        except Exception:
            # next records are not affected, therefore, if the first decoding
            # fails, it must be base64, since it should be untouched
            next_decoded_record = json.loads(base64.b64decode(record["Data"]).decode("ascii"))

        # if datastreams is enabled, we will have dd-pathway-ctx or dd-pathway-ctx-base64 in each record
        # we should NOT have dd trace context in each record though!
        if config._data_streams_enabled:
            assert "_datadog" in next_decoded_record
            assert HTTP_HEADER_TRACE_ID not in next_decoded_record["_datadog"]
            assert PROPAGATION_KEY_BASE_64 in next_decoded_record["_datadog"]
        else:
            assert "_datadog" not in next_decoded_record

        client.delete_stream(StreamName=stream_name)

        return decoded_record_data

    def _kinesis_generate_records(self, data, n):
        return [{"Data": data, "PartitionKey": "1234"} for _ in range(n)]

    @mock_kinesis
    def test_kinesis_put_record_json_string_trace_injection(self):
        # dict -> json string
        data = json.dumps({"json": "string"})

        self._test_kinesis_put_record_trace_injection("json_string", data)

    @mock_kinesis
    def test_kinesis_put_record_bytes_trace_injection(self):
        # dict -> json string -> bytes
        json_string = json.dumps({"json-string": "bytes"})
        data = json_string.encode()

        self._test_kinesis_put_record_trace_injection("json_string_bytes", data)

    @mock_kinesis
    def test_kinesis_put_record_base64_trace_injection(self):
        # dict -> json string -> bytes -> base64
        json_string = json.dumps({"json-string": "bytes-base64"})
        string_bytes = json_string.encode()
        data = base64.b64encode(string_bytes)

        self._test_kinesis_put_record_trace_injection("json_string_bytes_base64", data)

    @mock_kinesis
    def test_kinesis_put_record_base64_max_size(self):
        # dict -> json string -> bytes -> base64
        json_string = json.dumps({"json-string": "x" * (1 << 20)})
        string_bytes = json_string.encode()
        data = base64.b64encode(string_bytes)

        self._test_kinesis_put_record_trace_injection("json_string_bytes_base64_max_size", data)

    @mock_kinesis
    def test_kinesis_put_records_json_trace_injection(self):
        # (dict -> json string)[]
        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 2)

        self._test_kinesis_put_records_trace_injection("json_string", records)

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @unittest.skipIf(BOTOCORE_VERSION < (1, 26, 31), "Kinesis didn't support streamARN till 1.26.31")
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_kinesis_data_streams_enabled_put_records(self):
        with mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            # (dict -> json string)[]
            data = json.dumps({"json": "string"})
            records = self._kinesis_generate_records(data, 2)
            client = self.session.create_client("kinesis", region_name="us-east-1")

            self._test_kinesis_put_records_trace_injection(
                "data_streams", records, client=client, enable_stream_arn=True
            )

            pin = Pin.get_from(client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            in_tags = ",".join(
                [
                    "direction:in",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_records_data_streams",
                    "type:kinesis",
                ]
            )
            out_tags = ",".join(
                [
                    "direction:out",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_records_data_streams",
                    "type:kinesis",
                ]
            )
            assert (
                first[
                    (
                        in_tags,
                        7250761453654470644,
                        17012262583645342129,
                    )
                ].full_pathway_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        in_tags,
                        7250761453654470644,
                        17012262583645342129,
                    )
                ].edge_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        in_tags,
                        7250761453654470644,
                        17012262583645342129,
                    )
                ].payload_size.count
                == 2
            )
            assert (
                first[
                    (
                        out_tags,
                        17012262583645342129,
                        0,
                    )
                ].full_pathway_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        out_tags,
                        17012262583645342129,
                        0,
                    )
                ].edge_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        out_tags,
                        17012262583645342129,
                        0,
                    )
                ].payload_size.count
                == 2
            )

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @unittest.skipIf(BOTOCORE_VERSION < (1, 26, 31), "Kinesis didn't support streamARN till 1.26.31")
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_kinesis_data_streams_enabled_put_record(self):
        # (dict -> json string)[]
        with mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            data = json.dumps({"json": "string"})
            client = self.session.create_client("kinesis", region_name="us-east-1")

            self._test_kinesis_put_record_trace_injection("data_streams", data, client=client, enable_stream_arn=True)

            pin = Pin.get_from(client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats
            in_tags = ",".join(
                [
                    "direction:in",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_record_data_streams",
                    "type:kinesis",
                ]
            )
            out_tags = ",".join(
                [
                    "direction:out",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_record_data_streams",
                    "type:kinesis",
                ]
            )
            assert (
                first[
                    (
                        in_tags,
                        7186383338881463054,
                        14715769790627487616,
                    )
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        in_tags,
                        7186383338881463054,
                        14715769790627487616,
                    )
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        in_tags,
                        7186383338881463054,
                        14715769790627487616,
                    )
                ].payload_size.count
                == 1
            )
            assert (
                first[
                    (
                        out_tags,
                        14715769790627487616,
                        0,
                    )
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        out_tags,
                        14715769790627487616,
                        0,
                    )
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        out_tags,
                        14715769790627487616,
                        0,
                    )
                ].payload_size.count
                == 1
            )

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DATA_STREAMS_ENABLED="True",
            DD_BOTOCORE_DISTRIBUTED_TRACING="False",
            DD_BOTOCORE_PROPAGATION_ENABLED="False",
        )
    )
    @mock_kinesis
    def test_kinesis_put_records_inject_data_streams_to_every_record_propagation_disabled(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_inject_data_streams_to_every_record_context_prop_disabled"
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 5)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

        response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

        for record in response["Records"]:
            data = json.loads(record["Data"])
            assert "_datadog" in data
            assert PROPAGATION_KEY_BASE_64 in data["_datadog"]

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DATA_STREAMS_ENABLED="True",
            DD_BOTOCORE_DISTRIBUTED_TRACING="True",
            DD_BOTOCORE_PROPAGATION_ENABLED="True",
        )
    )
    @mock_kinesis
    def test_kinesis_put_records_inject_data_streams_to_every_record_propagation_enabled(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_inject_data_streams_to_every_record_prop_enabled"
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 5)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

        response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

        for record in response["Records"]:
            data = json.loads(record["Data"])
            assert "_datadog" in data
            assert PROPAGATION_KEY_BASE_64 in data["_datadog"]

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DATA_STREAMS_ENABLED="False",
            DD_BOTOCORE_DISTRIBUTED_TRACING="False",
            DD_BOTOCORE_PROPAGATION_ENABLED="False",
        )
    )
    @mock_kinesis
    def test_kinesis_put_records_inject_data_streams_to_every_record_disable_all_injection(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_inject_data_streams_to_every_record_all_injection_disabled"
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 5)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

        response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

        for record in response["Records"]:
            data = json.loads(record["Data"])
            assert "_datadog" not in data

    @mock_kinesis
    def test_kinesis_put_records_bytes_trace_injection(self):
        # dict -> json string -> bytes
        json_string = json.dumps({"json-string": "bytes"})
        data = json_string.encode()
        records = self._kinesis_generate_records(data, 2)

        self._test_kinesis_put_records_trace_injection("json_string_bytes", records)

    @mock_kinesis
    def test_kinesis_put_records_base64_trace_injection(self):
        # dict -> json string -> bytes
        json_string = json.dumps({"json-string": "bytes-base64"})
        string_bytes = json_string.encode()
        data = base64.b64encode(string_bytes)
        records = self._kinesis_generate_records(data, 2)

        self._test_kinesis_put_records_trace_injection("json_string_bytes_base64", records)

    @mock_kinesis
    def test_kinesis_put_records_newline_json_trace_injection(self):
        # (dict -> json string + new line)[]
        data = json.dumps({"json": "string"}) + "\n"
        records = self._kinesis_generate_records(data, 2)

        decoded_record_data = self._test_kinesis_put_records_trace_injection("json_string", records)

        assert decoded_record_data.endswith("\n")

    @mock_kinesis
    def test_kinesis_put_records_newline_bytes_trace_injection(self):
        # (dict -> json string -> bytes + new line)[]
        json_string = json.dumps({"json-string": "bytes"}) + "\n"
        data = json_string.encode()
        records = self._kinesis_generate_records(data, 2)

        decoded_record_data = self._test_kinesis_put_records_trace_injection("json_string", records)

        assert decoded_record_data.endswith("\n")

    @mock_kinesis
    def test_kinesis_parenting(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]

        Pin.get_from(client).clone(tracer=self.tracer).onto(client)

        with self.tracer.trace("kinesis.manual_span"):
            client.create_stream(StreamName=stream_name, ShardCount=1)
            client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()

        assert spans[0].name == "kinesis.manual_span"

        assert spans[1].service == "aws.kinesis"
        assert spans[1].name == "kinesis.command"
        assert spans[1].parent_id == spans[0].span_id

        assert spans[2].service == "aws.kinesis"
        assert spans[2].name == "kinesis.command"
        assert spans[2].parent_id == spans[0].span_id

    @mock_sqs
    def test_sqs_parenting(self):
        Pin.get_from(self.sqs_client).clone(tracer=self.tracer).onto(self.sqs_client)

        with self.tracer.trace("sqs.manual_span"):
            self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")

        spans = self.get_spans()

        assert spans[0].name == "sqs.manual_span"

        assert spans[1].service == "aws.sqs"
        assert spans[1].name == "sqs.command"
        assert spans[1].parent_id == spans[0].span_id

    @mock_kinesis
    def test_kinesis_put_records_newline_base64_trace_injection(self):
        # (dict -> json string -> bytes -> base64 + new line)[]
        json_string = json.dumps({"json-string": "bytes-base64"}) + "\n"
        string_bytes = json_string.encode("ascii")
        data = base64.b64encode(string_bytes)
        records = self._kinesis_generate_records(data, 2)

        decoded_record_data = self._test_kinesis_put_records_trace_injection("json_string", records)
        assert decoded_record_data.endswith("\n")

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_kinesis_client_default(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]

        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client.create_stream(StreamName=stream_name, ShardCount=1)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans[0].service == "aws.kinesis"
        assert spans[0].name == "kinesis.command"
        assert spans[1].service == "aws.kinesis"
        assert spans[1].name == "kinesis.command"

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_kinesis_client_v0(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client.create_stream(StreamName=stream_name, ShardCount=1)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans[0].service == "aws.kinesis"
        assert spans[0].name == "kinesis.command"
        assert spans[1].service == "aws.kinesis"
        assert spans[1].name == "kinesis.command"

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_kinesis_client_v1(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client.create_stream(StreamName=stream_name, ShardCount=1)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans[0].service == "mysvc"
        assert spans[0].name == "aws.kinesis.request"
        assert spans[1].service == "mysvc"
        assert spans[1].name == "aws.kinesis.send"

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_kinesis_client_default(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client.create_stream(StreamName=stream_name, ShardCount=1)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans[0].service == "aws.kinesis"
        assert spans[0].name == "kinesis.command"
        assert spans[1].service == "aws.kinesis"
        assert spans[1].name == "kinesis.command"

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_kinesis_client_v0(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client.create_stream(StreamName=stream_name, ShardCount=1)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans[0].service == "aws.kinesis"
        assert spans[0].name == "kinesis.command"
        assert spans[1].service == "aws.kinesis"
        assert spans[1].name == "kinesis.command"

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_kinesis_client_v1(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")
        stream_name = "test"

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]
        Pin.get_from(client).clone(tracer=self.tracer).onto(client)
        client.create_stream(StreamName=stream_name, ShardCount=1)
        client.put_records(StreamName=stream_name, Records=data)

        spans = self.get_spans()
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[0].name == "aws.kinesis.request"
        assert spans[1].service == DEFAULT_SPAN_SERVICE_NAME
        assert spans[1].name == "aws.kinesis.send"

    def test_secretsmanager(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.name == "secretsmanager.command"
            assert span.resource == "secretsmanager.createsecret"
            assert span.get_tag("params.Name") is None
            assert span.get_tag("aws.operation") == "CreateSecret"
            assert span.get_tag("aws.region") == "us-east-1"
            assert span.get_tag("region") == "us-east-1"
            assert span.get_tag("aws.agent") == "botocore"
            assert span.get_tag("http.status_code") == "200"
            assert span.get_tag("params.SecretString") is None
            assert span.get_tag("params.SecretBinary") is None

    def test_secretsmanager_binary(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretBinary=b"supersecret-binary")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.name == "secretsmanager.command"
            assert span.resource == "secretsmanager.createsecret"
            assert span.get_tag("params.Name") is None
            assert span.get_tag("aws.operation") == "CreateSecret"
            assert span.get_tag("aws.region") == "us-east-1"
            assert span.get_tag("region") == "us-east-1"
            assert span.get_tag("aws.agent") == "botocore"
            assert span.get_tag("http.status_code") == "200"
            assert span.get_tag("params.SecretString") is None
            assert span.get_tag("params.SecretBinary") is None

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_secretsmanager_default(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin.get_from(client).clone(tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.service == "aws.secretsmanager"
            assert span.name == "secretsmanager.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_secretsmanager_v0(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin.get_from(client).clone(tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.service == "aws.secretsmanager"
            assert span.name == "secretsmanager.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_secretsmanager_v1(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin.get_from(client).clone(tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.service == "mysvc"
            assert span.name == "aws.secretsmanager.request"

    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_secretsmanager_default(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin.get_from(client).clone(tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.service == "aws.secretsmanager"
            assert span.name == "secretsmanager.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_secretsmanager_v0(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin.get_from(client).clone(tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.service == "aws.secretsmanager"
            assert span.name == "secretsmanager.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_secretsmanager_v1(self):
        from moto import mock_secretsmanager

        with mock_secretsmanager():
            client = self.session.create_client("secretsmanager", region_name="us-east-1")
            Pin.get_from(client).clone(tracer=self.tracer).onto(client)

            resp = client.create_secret(Name="/my/secrets", SecretString="supersecret-string")
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]

            assert span.service == DEFAULT_SPAN_SERVICE_NAME
            assert span.name == "aws.secretsmanager.request"
