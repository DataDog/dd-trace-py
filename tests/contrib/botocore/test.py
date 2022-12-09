import base64
import datetime
import io
import json
import unittest
import zipfile

import botocore.exceptions
import botocore.session
from moto import mock_ec2
from moto import mock_events
from moto import mock_kinesis
from moto import mock_kms
from moto import mock_lambda
from moto import mock_s3
from moto import mock_sns
from moto import mock_sqs
import pytest


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
from ddtrace.contrib.botocore.patch import patch
from ddtrace.contrib.botocore.patch import unpatch
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import stringify
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

    def setUp(self):
        patch()

        self.session = botocore.session.get_session()
        self.session.set_credentials(access_key="access-key", secret_key="secret-key")

        super(BotocoreTest, self).setUp()

    def tearDown(self):
        super(BotocoreTest, self).tearDown()

        unpatch()

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
        assert span.get_tag("aws.operation") == "DescribeInstances"
        assert span.get_tag("aws.requestid") == "fdcdcab1-ae5c-489e-9c33-4637c5dda355"
        assert_span_http_status_code(span, 200)
        assert span.get_metric("retry_attempts") == 0
        assert span.service == "test-botocore-tracing.ec2"
        assert span.resource == "ec2.describeinstances"
        assert span.name == "ec2.command"
        assert span.span_type == "http"
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

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
        By default we attach exception information to s3 HeadObject
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

    @mock_s3
    def test_s3_put(self):
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
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.s3"
        assert span.resource == "s3.createbucket"
        assert spans[1].get_tag("aws.operation") == "PutObject"
        assert spans[1].resource == "s3.putobject"
        assert spans[1].get_tag("params.Key") == stringify(params["Key"])
        assert spans[1].get_tag("params.Bucket") == stringify(params["Bucket"])
        # confirm blacklisted
        assert spans[1].get_tag("params.Body") is None

    @mock_sqs
    def test_sqs_client(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)

        sqs.list_queues()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "ListQueues"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.listqueues"

    @mock_sqs
    def test_sqs_send_message_trace_injection_with_no_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)

        sqs.send_message(QueueUrl=queue["QueueUrl"], MessageBody="world")
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessage"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessage"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        trace_data_injected = json.loads(trace_json)
        assert trace_data_injected[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert trace_data_injected[HTTP_HEADER_PARENT_ID] == str(span.span_id)
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        assert len(response["Messages"]) == 1
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])
        trace_data_in_message = json.loads(trace_json_message)
        assert trace_data_in_message[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert trace_data_in_message[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sqs
    def test_sqs_send_message_distributed_tracing_off(self):
        with self.override_config("botocore", dict(distributed_tracing=False)):
            sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
            queue = sqs.create_queue(QueueName="test")
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)

            sqs.send_message(QueueUrl=queue["QueueUrl"], MessageBody="world")
            spans = self.get_spans()
            assert spans
            span = spans[0]
            assert len(spans) == 1
            assert span.get_tag("aws.region") == "us-east-1"
            assert span.get_tag("aws.operation") == "SendMessage"
            assert span.get_tag("params.MessageBody") is None
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            assert span.service == "test-botocore-tracing.sqs"
            assert span.resource == "sqs.sendmessage"
            assert span.get_tag("params.MessageAttributes._datadog.StringValue") is None

            response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
            assert len(response["Messages"]) == 1
            trace_in_message = "MessageAttributes" in response["Messages"][0]
            assert trace_in_message is False
            sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_sqs
    def test_sqs_send_message_trace_injection_with_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)
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
        sqs.send_message(QueueUrl=queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessage"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessage"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        trace_data_injected = json.loads(trace_json)
        assert trace_data_injected[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert trace_data_injected[HTTP_HEADER_PARENT_ID] == str(span.span_id)
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        assert len(response["Messages"]) == 1
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        assert trace_data_in_message[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert trace_data_in_message[HTTP_HEADER_PARENT_ID] == str(span.span_id)
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_sqs
    def test_sqs_send_message_trace_injection_with_max_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)
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
        sqs.send_message(QueueUrl=queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessage"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessage"
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        assert trace_json is None
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        assert len(response["Messages"]) == 1
        trace_in_message = "MessageAttributes" in response["Messages"][0]
        assert trace_in_message is False
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_sqs
    def test_sqs_send_message_batch_trace_injection_with_no_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)
        entries = [
            {
                "Id": "1",
                "MessageBody": "ironmaiden",
            }
        ]
        sqs.send_message_batch(QueueUrl=queue["QueueUrl"], Entries=entries)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessageBatch"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessagebatch"
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        assert len(response["Messages"]) == 1
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        assert trace_data_in_message[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert trace_data_in_message[HTTP_HEADER_PARENT_ID] == str(span.span_id)
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_sqs
    def test_sqs_send_message_batch_trace_injection_with_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)
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

        sqs.send_message_batch(QueueUrl=queue["QueueUrl"], Entries=entries)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessageBatch"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessagebatch"
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        assert len(response["Messages"]) == 1
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        assert trace_data_in_message[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert trace_data_in_message[HTTP_HEADER_PARENT_ID] == str(span.span_id)
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_sqs
    def test_sqs_send_message_batch_trace_injection_with_max_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)
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

        sqs.send_message_batch(QueueUrl=queue["QueueUrl"], Entries=entries)
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "SendMessageBatch"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.sqs"
        assert span.resource == "sqs.sendmessagebatch"
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        assert len(response["Messages"]) == 1
        trace_in_message = "MessageAttributes" in response["Messages"][0]
        assert trace_in_message is False
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_kinesis
    def test_kinesis_client(self):
        kinesis = self.session.create_client("kinesis", region_name="us-east-1")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(kinesis)

        kinesis.list_streams()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "ListStreams"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.liststreams"

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
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)

        patch()
        patch()

        sqs.list_queues()

        spans = self.get_spans()
        assert spans
        assert len(spans) == 1

    @mock_lambda
    def test_lambda_client(self):
        lamb = self.session.create_client("lambda", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-west-2"
        assert span.get_tag("aws.operation") == "ListFunctions"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.lambda"
        assert span.resource == "lambda.listfunctions"

    @mock_lambda
    def test_lambda_invoke_no_context_client(self):
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
        assert span.get_tag("aws.operation") == "Invoke"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.lambda"
        assert span.resource == "lambda.invoke"
        context_b64 = span.get_tag("params.ClientContext")
        context_json = base64.b64decode(context_b64.encode()).decode()
        context_obj = json.loads(context_json)

        assert context_obj["custom"][HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert context_obj["custom"][HTTP_HEADER_PARENT_ID] == str(span.span_id)

        lamb.delete_function(FunctionName="ironmaiden")

    @mock_lambda
    def test_lambda_invoke_with_old_style_trace_propagation(self):
        with self.override_config("botocore", dict(invoke_with_legacy_context=True)):
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
            assert span.get_tag("aws.operation") == "Invoke"
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            assert span.service == "test-botocore-tracing.lambda"
            assert span.resource == "lambda.invoke"
            context_b64 = span.get_tag("params.ClientContext")
            context_json = base64.b64decode(context_b64.encode()).decode()
            context_obj = json.loads(context_json)

            assert context_obj["custom"]["_datadog"][HTTP_HEADER_TRACE_ID] == str(span.trace_id)
            assert context_obj["custom"]["_datadog"][HTTP_HEADER_PARENT_ID] == str(span.span_id)

            lamb.delete_function(FunctionName="ironmaiden")

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
            assert span.get_tag("aws.operation") == "Invoke"
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            assert span.service == "test-botocore-tracing.lambda"
            assert span.resource == "lambda.invoke"
            assert span.get_tag("params.ClientContext") is None
            lamb.delete_function(FunctionName="ironmaiden")

    @mock_lambda
    def test_lambda_invoke_with_context_client(self):
        lamb = self.session.create_client("lambda", region_name="us-west-2", endpoint_url="http://localhost:4566")
        lamb.create_function(
            FunctionName="megadeth",
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
        client_context = base64.b64encode(json.dumps({"custom": {"foo": "bar"}}).encode()).decode()

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(lamb)

        lamb.invoke(
            FunctionName="megadeth",
            ClientContext=client_context,
            Payload=json.dumps({}),
        )

        spans = self.get_spans()
        assert spans
        span = spans[0]

        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-west-2"
        assert span.get_tag("aws.operation") == "Invoke"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.lambda"
        assert span.resource == "lambda.invoke"
        context_b64 = span.get_tag("params.ClientContext")
        context_json = base64.b64decode(context_b64.encode()).decode()
        context_obj = json.loads(context_json)

        assert context_obj["custom"]["foo"] == "bar"
        assert context_obj["custom"][HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert context_obj["custom"][HTTP_HEADER_PARENT_ID] == str(span.span_id)

        lamb.delete_function(FunctionName="megadeth")

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
        assert span.get_tag("aws.operation") == "Invoke"
        assert_is_measured(span)
        lamb.delete_function(FunctionName="black-sabbath")

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
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        queue_url = queue["QueueUrl"]
        bridge.put_targets(
            Rule="a-test-bus-rule",
            Targets=[{"Id": "a-test-bus-rule-target", "Arn": "arn:aws:sqs:us-east-1:000000000000:test"}],
        )

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(bridge)
        bridge.put_events(Entries=entries)

        messages = sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=2)

        bridge.delete_event_bus(Name="a-test-bus")
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

        spans = self.get_spans()
        assert spans
        assert len(spans) == 2
        span = spans[0]
        str_entries = span.get_tag("params.Entries")
        assert str_entries is None

        message = messages["Messages"][0]
        body = message.get("Body")
        assert body is not None
        # body_obj = ast.literal_eval(body)
        body_obj = json.loads(body)
        detail = body_obj.get("detail")
        headers = detail.get("_datadog")
        assert headers is not None
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_events
    def test_eventbridge_muliple_entries_trace_injection(self):
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
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        queue_url = queue["QueueUrl"]
        bridge.put_targets(
            Rule="a-test-bus-rule",
            Targets=[{"Id": "a-test-bus-rule-target", "Arn": "arn:aws:sqs:us-east-1:000000000000:test"}],
        )

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(bridge)
        bridge.put_events(Entries=entries)

        messages = sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=2)

        bridge.delete_event_bus(Name="a-test-bus")
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

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
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
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
        # assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
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
        assert span.get_tag("aws.operation") == "ListKeys"
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kms"
        assert span.resource == "kms.listkeys"

        # checking for protection on sts against security leak
        assert span.get_tag("params") is None

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
        assert dd_span.get_tag("aws.operation") == "DescribeInstances"
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

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT="true"))
    def test_invoke_legacy_context_env_override(self):
        assert config.botocore.invoke_with_legacy_context is True

    @mock_sns
    @mock_sqs
    def test_sns_send_message_trace_injection_with_no_message_attributes(self):
        sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")
        queue = sqs.create_queue(QueueName="test")

        topic_arn = topic["TopicArn"]
        sqs_url = queue["QueueUrl"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_url)

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

        sns.publish(TopicArn=topic_arn, Message="test")
        spans = self.get_spans()

        # get SNS messages via SQS
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], WaitTimeSeconds=2)

        # clean up resources
        sqs.delete_queue(QueueUrl=sqs_url)
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "Publish"
        assert span.get_tag("params.MessageBody") is None
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
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sns
    @mock_sqs
    @pytest.mark.xfail(strict=False)  # FIXME: flaky test
    def test_sns_send_message_trace_injection_with_message_attributes(self):
        sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")
        queue = sqs.create_queue(QueueName="test")

        topic_arn = topic["TopicArn"]
        sqs_url = queue["QueueUrl"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_url)

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
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])

        # clean up resources
        sqs.delete_queue(QueueUrl=sqs_url)
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "Publish"
        assert span.get_tag("params.MessageBody") is None
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
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    @mock_sns
    @mock_sqs
    def test_sns_send_message_trace_injection_with_max_message_attributes(self):
        sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")

        topic = sns.create_topic(Name="testTopic")
        queue = sqs.create_queue(QueueName="test")

        topic_arn = topic["TopicArn"]
        sqs_url = queue["QueueUrl"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_url)

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
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], WaitTimeSeconds=2)

        # clean up resources
        sqs.delete_queue(QueueUrl=sqs_url)
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        assert len(spans) == 2
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "Publish"
        assert span.get_tag("params.MessageBody") is None
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

    # NOTE: commenting out the tests below because localstack has a bug where messages
    # published to SNS via publish_batch and retrieved via SQS are missing MessageAttributes
    # Reported a bug here: https://github.com/localstack/localstack/issues/5395

    # @mock_sns
    # @mock_sqs
    # def test_sns_send_message_batch_trace_injection_with_no_message_attributes(self):
    #     sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")
    #     sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")

    #     topic = sns.create_topic(Name="testTopic")
    #     queue = sqs.create_queue(QueueName="test")

    #     topic_arn = topic["TopicArn"]
    #     sqs_url = queue["QueueUrl"]
    #     sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_url)

    #     Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)
    #     entries = [
    #         {
    #             "Id": "1",
    #             "Message": "ironmaiden",
    #         },
    #         {
    #             "Id": "2",
    #             "Message": "megadeth",
    #         },
    #     ]
    #     sns.publish_batch(TopicArn=topic_arn, PublishBatchRequestEntries=entries)
    #     spans = self.get_spans()

    #     # get SNS messages via SQS
    #     response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])

    #     # clean up resources
    #     sqs.delete_queue(QueueUrl=sqs_url)
    #     sns.delete_topic(TopicArn=topic_arn)

    #     # check if the appropriate span was generated
    #     assert spans
    #     span = spans[0]
    #     assert len(spans) == 2
    #     assert span.get_tag("aws.region") == "us-east-1"
    #     assert span.get_tag("aws.operation") == "PublishBatch"
    #     assert span.get_tag("params.MessageBody") is None
    #     assert_is_measured(span)
    #     assert_span_http_status_code(span, 200)
    #     assert span.service == "test-botocore-tracing.sns"
    #     assert span.resource == "sns.publishbatch"

    #     # receive message using SQS and ensure headers are present
    #     assert len(response["Messages"]) == 1
    #     msg = response["Messages"][0]
    #     assert msg is not None
    #     msg_body = json.loads(msg["Body"])
    #     msg_str = msg_body["Message"]
    #     assert msg_str == "test"
    #     msg_attr = msg_body["MessageAttributes"]
    #     assert msg_attr.get("_datadog") is not None
    #     headers = json.loads(msg_attr["_datadog"]["Value"])
    #     assert headers is not None
    #     assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
    #     assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    # @mock_sns
    # @mock_sqs
    # def test_sns_send_message_batch_trace_injection_with_message_attributes(self):
    #     sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")
    #     sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")

    #     topic = sns.create_topic(Name="testTopic")
    #     queue = sqs.create_queue(QueueName="test")

    #     topic_arn = topic["TopicArn"]
    #     sqs_url = queue["QueueUrl"]
    #     sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_url)

    #     Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

    #     message_attributes = {
    #         "one": {"DataType": "String", "StringValue": "one"},
    #         "two": {"DataType": "String", "StringValue": "two"},
    #         "three": {"DataType": "String", "StringValue": "three"},
    #         "four": {"DataType": "String", "StringValue": "four"},
    #         "five": {"DataType": "String", "StringValue": "five"},
    #         "six": {"DataType": "String", "StringValue": "six"},
    #         "seven": {"DataType": "String", "StringValue": "seven"},
    #         "eight": {"DataType": "String", "StringValue": "eight"},
    #         "nine": {"DataType": "String", "StringValue": "nine"},
    #     }
    #     entries = [
    #         {"Id": "1", "Message": "ironmaiden", "MessageAttributes": message_attributes},
    #         {"Id": "2", "Message": "megadeth", "MessageAttributes": message_attributes},
    #     ]
    #     sns.publish_batch(TopicArn=topic_arn, PublishBatchRequestEntries=entries)
    #     spans = self.get_spans()

    #     # get SNS messages via SQS
    #     response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])

    #     # clean up resources
    #     sqs.delete_queue(QueueUrl=sqs_url)
    #     sns.delete_topic(TopicArn=topic_arn)

    #     # check if the appropriate span was generated
    #     assert spans
    #     span = spans[0]
    #     assert len(spans) == 2
    #     assert span.get_tag("aws.region") == "us-east-1"
    #     assert span.get_tag("aws.operation") == "PublishBatch"
    #     assert span.get_tag("params.MessageBody") is None
    #     assert_is_measured(span)
    #     assert_span_http_status_code(span, 200)
    #     assert span.service == "test-botocore-tracing.sns"
    #     assert span.resource == "sns.publishbatch"

    #     # receive message using SQS and ensure headers are present
    #     assert len(response["Messages"]) == 1
    #     msg = response["Messages"][0]
    #     assert msg is not None
    #     msg_body = json.loads(msg["Body"])
    #     msg_str = msg_body["Message"]
    #     assert msg_str == "test"
    #     msg_attr = msg_body["MessageAttributes"]
    #     assert msg_attr.get("_datadog") is not None
    #     headers = json.loads(msg_attr["_datadog"]["Value"])
    #     assert headers is not None
    #     assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
    #     assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

    # @mock_sns
    # @mock_sqs
    # def test_sns_send_message_batch_trace_injection_with_max_message_attributes(self):
    #     sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")
    #     sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")

    #     topic = sns.create_topic(Name="testTopic")
    #     queue = sqs.create_queue(QueueName="test")

    #     topic_arn = topic["TopicArn"]
    #     sqs_url = queue["QueueUrl"]
    #     sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_url)

    #     Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sns)

    #     message_attributes = {
    #         "one": {"DataType": "String", "StringValue": "one"},
    #         "two": {"DataType": "String", "StringValue": "two"},
    #         "three": {"DataType": "String", "StringValue": "three"},
    #         "four": {"DataType": "String", "StringValue": "four"},
    #         "five": {"DataType": "String", "StringValue": "five"},
    #         "six": {"DataType": "String", "StringValue": "six"},
    #         "seven": {"DataType": "String", "StringValue": "seven"},
    #         "eight": {"DataType": "String", "StringValue": "eight"},
    #         "nine": {"DataType": "String", "StringValue": "nine"},
    #         "ten": {"DataType": "String", "StringValue": "ten"},
    #     }
    #     entries = [
    #         {"Id": "1", "Message": "ironmaiden", "MessageAttributes": message_attributes},
    #         {"Id": "2", "Message": "megadeth", "MessageAttributes": message_attributes},
    #     ]
    #     sns.publish(TopicArn=topic_arn, PublishBatchRequestEntries=entries)
    #     spans = self.get_spans()

    #     # get SNS messages via SQS
    #     response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])

    #     # clean up resources
    #     sqs.delete_queue(QueueUrl=sqs_url)
    #     sns.delete_topic(TopicArn=topic_arn)

    #     # check if the appropriate span was generated
    #     assert spans
    #     span = spans[0]
    #     assert len(spans) == 2
    #     assert span.get_tag("aws.region") == "us-east-1"
    #     assert span.get_tag("aws.operation") == "Publish"
    #     assert span.get_tag("params.MessageBody") is None
    #     assert_is_measured(span)
    #     assert_span_http_status_code(span, 200)
    #     assert span.service == "test-botocore-tracing.sns"
    #     assert span.resource == "sns.publish"
    #     trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
    #     assert trace_json is None

    #     # receive message using SQS and ensure headers are present
    #     assert len(response["Messages"]) == 1
    #     msg = response["Messages"][0]
    #     assert msg is not None
    #     msg_body = json.loads(msg["Body"])
    #     msg_str = msg_body["Message"]
    #     assert msg_str == "test"
    #     msg_attr = msg_body["MessageAttributes"]
    #     assert msg_attr.get("_datadog") is None

    @mock_kinesis
    def test_kinesis_put_record_json_trace_injection(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "test"
        client.create_stream(StreamName=stream_name, ShardCount=1)
        stream = client.describe_stream(StreamName=stream_name)["StreamDescription"]
        shard_id = stream["Shards"][0]["ShardId"]

        data = json.dumps({"Hello": "World"})
        partition_key = "1234"

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key)

        # check if the appropriate span was generated
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "PutRecord"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.putrecord"
        trace_json = span.get_tag("params.Data")
        assert trace_json is None

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        assert len(resp["Records"]) == 1
        record = resp["Records"][0]
        assert record["Data"] is not None
        data = json.loads(record["Data"].decode("ascii"))
        headers = data["_datadog"]
        assert headers is not None
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

        client.delete_stream(StreamName=stream_name)

    @mock_kinesis
    def test_kinesis_put_record_base64_trace_injection(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "test"
        client.create_stream(StreamName=stream_name, ShardCount=1)
        stream = client.describe_stream(StreamName=stream_name)["StreamDescription"]
        shard_id = stream["Shards"][0]["ShardId"]

        sample_string = json.dumps({"Hello": "World"})
        sample_string_bytes = sample_string.encode("ascii")
        base64_bytes = base64.b64encode(sample_string_bytes)
        data = base64_bytes.decode("ascii")

        partition_key = "1234"

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key)

        # check if the appropriate span was generated
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "PutRecord"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.putrecord"
        trace_json = span.get_tag("params.Data")
        assert trace_json is None

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        assert len(resp["Records"]) == 1
        record = resp["Records"][0]
        assert record["Data"] is not None
        data = json.loads(record["Data"].decode("ascii"))
        headers = data["_datadog"]
        assert headers is not None
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

        client.delete_stream(StreamName=stream_name)

    @mock_kinesis
    def test_kinesis_put_record_base64_max_size(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "test"
        client.create_stream(StreamName=stream_name, ShardCount=1)
        stream = client.describe_stream(StreamName=stream_name)["StreamDescription"]
        shard_id = stream["Shards"][0]["ShardId"]

        sample_string = json.dumps({"Hello": "x" * (1 << 20)})
        sample_string_bytes = sample_string.encode("ascii")
        base64_bytes = base64.b64encode(sample_string_bytes)
        data = base64_bytes.decode("ascii")

        partition_key = "1234"

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key)

        # check if the appropriate span was generated
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "PutRecord"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.putrecord"
        trace_json = span.get_tag("params.Data")
        assert trace_json is None

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        assert len(resp["Records"]) == 1
        record = resp["Records"][0]
        assert record["Data"] is not None
        data = json.loads(base64.b64decode(record["Data"]).decode("ascii"))
        assert "_datadog" not in data

        client.delete_stream(StreamName=stream_name)

    @mock_kinesis
    def test_kinesis_put_records_json_trace_injection(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "test"
        client.create_stream(StreamName=stream_name, ShardCount=1)
        stream = client.describe_stream(StreamName=stream_name)["StreamDescription"]
        shard_id = stream["Shards"][0]["ShardId"]

        partition_key = "1234"
        data = [
            {"Data": json.dumps({"Hello": "World"}), "PartitionKey": partition_key},
            {"Data": json.dumps({"foo": "bar"}), "PartitionKey": partition_key},
        ]

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_records(StreamName=stream_name, Records=data)

        # check if the appropriate span was generated
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "PutRecords"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.putrecords"
        records = span.get_tag("params.Records")
        assert records is None

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        assert len(resp["Records"]) == 2
        records = resp["Records"]
        record = records[0]
        headers = json.loads(record["Data"].decode("ascii"))["_datadog"]
        assert headers is not None
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

        record = records[1]
        data = json.loads(record["Data"].decode("ascii"))
        assert "_datadog" not in data

        client.delete_stream(StreamName=stream_name)

    @mock_kinesis
    def test_kinesis_put_records_base64_trace_injection(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "test"
        client.create_stream(StreamName=stream_name, ShardCount=1)
        stream = client.describe_stream(StreamName=stream_name)["StreamDescription"]
        shard_id = stream["Shards"][0]["ShardId"]

        partition_key = "1234"
        sample_string = json.dumps({"Hello": "World"})
        sample_string_bytes = sample_string.encode("ascii")
        base64_bytes = base64.b64encode(sample_string_bytes)
        data_str = base64_bytes.decode("ascii")
        data = [
            {"Data": data_str, "PartitionKey": partition_key},
            {"Data": data_str, "PartitionKey": partition_key},
        ]

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_records(StreamName=stream_name, Records=data)

        # check if the appropriate span was generated
        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert len(spans) == 1
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("aws.operation") == "PutRecords"
        assert span.get_tag("params.MessageBody") is None
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        assert span.service == "test-botocore-tracing.kinesis"
        assert span.resource == "kinesis.putrecords"
        records = span.get_tag("params.Records")
        assert records is None

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        assert len(resp["Records"]) == 2
        records = resp["Records"]
        record = records[0]
        headers = json.loads(record["Data"].decode("ascii"))["_datadog"]
        assert headers is not None
        assert headers[HTTP_HEADER_TRACE_ID] == str(span.trace_id)
        assert headers[HTTP_HEADER_PARENT_ID] == str(span.span_id)

        record = records[1]
        data = json.loads(base64.b64decode(record["Data"]).decode("ascii"))
        assert "_datadog" not in data

        client.delete_stream(StreamName=stream_name)

    @unittest.skipIf(PY2, "Skipping for Python 2.7 since older moto doesn't support secretsmanager")
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
            assert span.get_tag("params.Name") == "/my/secrets"
            assert span.get_tag("aws.operation") == "CreateSecret"
            assert span.get_tag("aws.region") == "us-east-1"
            assert span.get_tag("aws.agent") == "botocore"
            assert span.get_tag("http.status_code") == "200"
            assert span.get_tag("params.SecretString") is None
            assert span.get_tag("params.SecretBinary") is None

    @unittest.skipIf(PY2, "Skipping for Python 2.7 since older moto doesn't support secretsmanager")
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
            assert span.get_tag("params.Name") == "/my/secrets"
            assert span.get_tag("aws.operation") == "CreateSecret"
            assert span.get_tag("aws.region") == "us-east-1"
            assert span.get_tag("aws.agent") == "botocore"
            assert span.get_tag("http.status_code") == "200"
            assert span.get_tag("params.SecretString") is None
            assert span.get_tag("params.SecretBinary") is None
