import base64
import datetime
import ast
import io
import json
import unittest
import zipfile

import botocore.session
from moto import mock_ec2
from moto import mock_events
from moto import mock_kinesis
from moto import mock_kms
from moto import mock_lambda
from moto import mock_s3
from moto import mock_sqs
from moto import mock_sns


# Older version of moto used kinesis to mock firehose
try:
    from moto import mock_firehose
except ImportError:
    from moto import mock_kinesis as mock_firehose

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.botocore.patch import patch
from ddtrace.contrib.botocore.patch import unpatch
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
        self.assertEqual(len(spans), 1)
        assert_is_measured(span)
        self.assertEqual(span.get_tag("aws.agent"), "botocore")
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("aws.operation"), "DescribeInstances")
        self.assertEqual(span.get_tag("aws.requestid"), "fdcdcab1-ae5c-489e-9c33-4637c5dda355")
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_metric("retry_attempts"), 0)
        self.assertEqual(span.service, "test-botocore-tracing.ec2")
        self.assertEqual(span.resource, "ec2.describeinstances")
        self.assertEqual(span.name, "ec2.command")
        self.assertEqual(span.span_type, "http")
        self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    @mock_ec2
    def test_traced_client_analytics(self):
        with self.override_config("botocore", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            ec2 = self.session.create_client("ec2", region_name="us-west-2")
            Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(ec2)
            ec2.describe_instances()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    @mock_s3
    def test_s3_client(self):
        s3 = self.session.create_client("s3", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(s3)

        s3.list_buckets()
        s3.list_buckets()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 2)
        assert_is_measured(span)
        self.assertEqual(span.get_tag("aws.operation"), "ListBuckets")
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.s3")
        self.assertEqual(span.resource, "s3.listbuckets")

        # testing for span error
        self.reset()
        try:
            s3.list_objects(bucket="mybucket")
        except Exception:
            spans = self.get_spans()
            assert spans
            span = spans[0]
            self.assertEqual(span.error, 1)
            self.assertEqual(span.resource, "s3.listobjects")

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
        self.assertEqual(len(spans), 2)
        self.assertEqual(span.get_tag("aws.operation"), "CreateBucket")
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.s3")
        self.assertEqual(span.resource, "s3.createbucket")
        self.assertEqual(spans[1].get_tag("aws.operation"), "PutObject")
        self.assertEqual(spans[1].resource, "s3.putobject")
        self.assertEqual(spans[1].get_tag("params.Key"), stringify(params["Key"]))
        self.assertEqual(spans[1].get_tag("params.Bucket"), stringify(params["Bucket"]))
        # confirm blacklisted
        self.assertIsNone(spans[1].get_tag("params.Body"))

    @mock_sqs
    def test_sqs_client(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)

        sqs.list_queues()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "ListQueues")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.listqueues")

    @mock_sqs
    def test_sqs_send_message_trace_injection_with_no_message_attributes(self):
        sqs = self.session.create_client("sqs", region_name="us-east-1", endpoint_url="http://localhost:4566")
        queue = sqs.create_queue(QueueName="test")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(sqs)

        sqs.send_message(QueueUrl=queue["QueueUrl"], MessageBody="world")
        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "SendMessage")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.sendmessage")
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        trace_data_injected = json.loads(trace_json)
        self.assertEqual(trace_data_injected[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_injected[HTTP_HEADER_PARENT_ID], str(span.span_id))
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        self.assertEqual(len(response["Messages"]), 1)
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])
        trace_data_in_message = json.loads(trace_json_message)
        self.assertEqual(trace_data_in_message[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_in_message[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
            self.assertEqual(len(spans), 1)
            self.assertEqual(span.get_tag("aws.region"), "us-east-1")
            self.assertEqual(span.get_tag("aws.operation"), "SendMessage")
            self.assertIsNone(span.get_tag("params.MessageBody"))
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            self.assertEqual(span.service, "test-botocore-tracing.sqs")
            self.assertEqual(span.resource, "sqs.sendmessage")
            self.assertEqual(span.get_tag("params.MessageAttributes._datadog.StringValue"), None)

            response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
            self.assertEqual(len(response["Messages"]), 1)
            trace_in_message = "MessageAttributes" in response["Messages"][0]
            self.assertEqual(trace_in_message, False)
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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "SendMessage")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.sendmessage")
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        trace_data_injected = json.loads(trace_json)
        self.assertEqual(trace_data_injected[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_injected[HTTP_HEADER_PARENT_ID], str(span.span_id))
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        self.assertEqual(len(response["Messages"]), 1)
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        self.assertEqual(trace_data_in_message[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_in_message[HTTP_HEADER_PARENT_ID], str(span.span_id))
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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "SendMessage")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.sendmessage")
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        self.assertEqual(trace_json, None)
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        self.assertEqual(len(response["Messages"]), 1)
        trace_in_message = "MessageAttributes" in response["Messages"][0]
        self.assertEqual(trace_in_message, False)
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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "SendMessageBatch")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.sendmessagebatch")
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        self.assertEqual(len(response["Messages"]), 1)
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        self.assertEqual(trace_data_in_message[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_in_message[HTTP_HEADER_PARENT_ID], str(span.span_id))
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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "SendMessageBatch")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.sendmessagebatch")
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        self.assertEqual(len(response["Messages"]), 1)
        trace_json_message = response["Messages"][0]["MessageAttributes"]["_datadog"]["StringValue"]
        trace_data_in_message = json.loads(trace_json_message)
        self.assertEqual(trace_data_in_message[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_in_message[HTTP_HEADER_PARENT_ID], str(span.span_id))
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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "SendMessageBatch")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sqs")
        self.assertEqual(span.resource, "sqs.sendmessagebatch")
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])
        self.assertEqual(len(response["Messages"]), 1)
        trace_in_message = "MessageAttributes" in response["Messages"][0]
        self.assertEqual(trace_in_message, False)
        sqs.delete_queue(QueueUrl=queue["QueueUrl"])

    @mock_kinesis
    def test_kinesis_client(self):
        kinesis = self.session.create_client("kinesis", region_name="us-east-1")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(kinesis)

        kinesis.list_streams()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "ListStreams")
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.kinesis")
        self.assertEqual(span.resource, "kinesis.liststreams")

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
        self.assertEqual(len(spans), 1)

    @mock_lambda
    def test_lambda_client(self):
        lamb = self.session.create_client("lambda", region_name="us-west-2")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("aws.operation"), "ListFunctions")
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.lambda")
        self.assertEqual(span.resource, "lambda.listfunctions")

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

        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("aws.operation"), "Invoke")
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.lambda")
        self.assertEqual(span.resource, "lambda.invoke")
        context_b64 = span.get_tag("params.ClientContext")
        context_json = base64.b64decode(context_b64.encode()).decode()
        context_obj = json.loads(context_json)

        self.assertEqual(context_obj["custom"][HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(context_obj["custom"][HTTP_HEADER_PARENT_ID], str(span.span_id))

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

            self.assertEqual(len(spans), 1)
            self.assertEqual(span.get_tag("aws.region"), "us-west-2")
            self.assertEqual(span.get_tag("aws.operation"), "Invoke")
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            self.assertEqual(span.service, "test-botocore-tracing.lambda")
            self.assertEqual(span.resource, "lambda.invoke")
            context_b64 = span.get_tag("params.ClientContext")
            context_json = base64.b64decode(context_b64.encode()).decode()
            context_obj = json.loads(context_json)

            self.assertEqual(context_obj["custom"]["_datadog"][HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(context_obj["custom"]["_datadog"][HTTP_HEADER_PARENT_ID], str(span.span_id))

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

            self.assertEqual(len(spans), 1)
            self.assertEqual(span.get_tag("aws.region"), "us-west-2")
            self.assertEqual(span.get_tag("aws.operation"), "Invoke")
            assert_is_measured(span)
            assert_span_http_status_code(span, 200)
            self.assertEqual(span.service, "test-botocore-tracing.lambda")
            self.assertEqual(span.resource, "lambda.invoke")
            self.assertEqual(span.get_tag("params.ClientContext"), None)
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

        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("aws.operation"), "Invoke")
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.lambda")
        self.assertEqual(span.resource, "lambda.invoke")
        context_b64 = span.get_tag("params.ClientContext")
        context_json = base64.b64decode(context_b64.encode()).decode()
        context_obj = json.loads(context_json)

        self.assertEqual(context_obj["custom"]["foo"], "bar")
        self.assertEqual(context_obj["custom"][HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(context_obj["custom"][HTTP_HEADER_PARENT_ID], str(span.span_id))

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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("aws.operation"), "Invoke")
        assert_is_measured(span)
        lamb.delete_function(FunctionName="black-sabbath")

    @mock_events
    def test_eventbridge_single_entry_trace_injection(self):
        bridge = self.session.create_client("events", region_name="us-west-2")
        bridge.create_event_bus(Name="a-test-bus")

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(bridge)

        entries = [
            {
                "Source": "some-event-source",
                "DetailType": "some-event-detail-type",
                "Detail": json.dumps({"foo": "bar"}),
                "EventBusName": "a-test-bus",
            }
        ]
        bridge.put_events(Entries=entries)

        spans = self.get_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]

        str_entries = span.get_tag("params.Entries")
        entries = ast.literal_eval(str_entries)
        print(entries)
        self.assertEqual(len(entries), 1)
        for e in entries:
            self.assertTrue("Detail" in e)
            detail = json.loads(e["Detail"])
            self.assertTrue(HTTP_HEADER_PARENT_ID in detail["_datadog"])
            self.assertTrue(HTTP_HEADER_TRACE_ID in detail["_datadog"])
            headers = detail["_datadog"]
            self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        bridge.delete_event_bus(Name="a-test-bus")

    @mock_events
    def test_eventbridge_muliple_entries_trace_injection(self):
        bridge = self.session.create_client("events", region_name="us-west-2")
        bridge.create_event_bus(Name="a-test-bus")

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(bridge)

        entries = [
            {
                "Source": "some-event-source",
                "DetailType": "some-event-detail-type",
                "Detail": json.dumps({"foo": "bar"}),
                "EventBusName": "a-test-bus",
            },
            {
                "Source": "another-event-source",
                "DetailType": "a-different-event-detail-type",
                "Detail": json.dumps({"foo": "bar"}),
                "EventBusName": "a-test-bus",
            },
        ]
        bridge.put_events(Entries=entries)

        spans = self.get_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]

        str_entries = span.get_tag("params.Entries")
        entries = ast.literal_eval(str_entries)
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertTrue("Detail" in e)
            detail = json.loads(e["Detail"])
            self.assertTrue(HTTP_HEADER_PARENT_ID in detail["_datadog"])
            self.assertTrue(HTTP_HEADER_TRACE_ID in detail["_datadog"])
            headers = detail["_datadog"]
            self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        bridge.delete_event_bus(Name="a-test-bus")

    @mock_kms
    def test_kms_client(self):
        kms = self.session.create_client("kms", region_name="us-east-1")
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(kms)

        kms.list_keys(Limit=21)

        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "ListKeys")
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.kms")
        self.assertEqual(span.resource, "kms.listkeys")

        # checking for protection on sts against security leak
        self.assertIsNone(span.get_tag("params"))

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
        self.assertEqual(len(spans), 2)

        ot_span, dd_span = spans

        # confirm the parenting
        self.assertIsNone(ot_span.parent_id)
        self.assertEqual(dd_span.parent_id, ot_span.span_id)

        self.assertEqual(ot_span.name, "ec2_op")
        self.assertEqual(ot_span.service, "ec2_svc")

        self.assertEqual(dd_span.get_tag("aws.agent"), "botocore")
        self.assertEqual(dd_span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(dd_span.get_tag("aws.operation"), "DescribeInstances")
        assert_span_http_status_code(dd_span, 200)
        self.assertEqual(dd_span.get_metric("retry_attempts"), 0)
        self.assertEqual(dd_span.service, "test-botocore-tracing.ec2")
        self.assertEqual(dd_span.resource, "ec2.describeinstances")
        self.assertEqual(dd_span.name, "ec2.command")

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
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])

        # clean up resources
        sqs.delete_queue(QueueUrl=sqs_url)
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 2)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "Publish")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sns")
        self.assertEqual(span.resource, "sns.publish")
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        trace_data_injected = json.loads(trace_json)
        self.assertEqual(trace_data_injected[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_injected[HTTP_HEADER_PARENT_ID], str(span.span_id))

        # receive message using SQS and ensure headers are present
        self.assertEqual(len(response["Messages"]), 1)
        msg = response["Messages"][0]
        self.assertIsNotNone(msg)
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        self.assertEqual(msg_str, "test")
        msg_attr = msg_body["MessageAttributes"]
        self.assertIsNotNone(msg_attr.get("_datadog"))
        headers = json.loads(msg_attr["_datadog"]["Value"])
        self.assertIsNotNone(headers)
        self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

    @mock_sns
    @mock_sqs
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
        self.assertEqual(len(spans), 2)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "Publish")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sns")
        self.assertEqual(span.resource, "sns.publish")
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        trace_data_injected = json.loads(trace_json)
        self.assertEqual(trace_data_injected[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(trace_data_injected[HTTP_HEADER_PARENT_ID], str(span.span_id))

        # receive message using SQS and ensure headers are present
        self.assertEqual(len(response["Messages"]), 1)
        msg = response["Messages"][0]
        self.assertIsNotNone(msg)
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        self.assertEqual(msg_str, "test")
        msg_attr = msg_body["MessageAttributes"]
        self.assertIsNotNone(msg_attr.get("_datadog"))
        headers = json.loads(msg_attr["_datadog"]["Value"])
        self.assertIsNotNone(headers)
        self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
        response = sqs.receive_message(QueueUrl=queue["QueueUrl"], MessageAttributeNames=["_datadog"])

        # clean up resources
        sqs.delete_queue(QueueUrl=sqs_url)
        sns.delete_topic(TopicArn=topic_arn)

        # check if the appropriate span was generated
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 2)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "Publish")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.sns")
        self.assertEqual(span.resource, "sns.publish")
        trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
        self.assertIsNone(trace_json)

        # receive message using SQS and ensure headers are present
        self.assertEqual(len(response["Messages"]), 1)
        msg = response["Messages"][0]
        self.assertIsNotNone(msg)
        msg_body = json.loads(msg["Body"])
        msg_str = msg_body["Message"]
        self.assertEqual(msg_str, "test")
        msg_attr = msg_body["MessageAttributes"]
        self.assertIsNone(msg_attr.get("_datadog"))

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
    #     self.assertEqual(len(spans), 2)
    #     self.assertEqual(span.get_tag("aws.region"), "us-east-1")
    #     self.assertEqual(span.get_tag("aws.operation"), "PublishBatch")
    #     self.assertIsNone(span.get_tag("params.MessageBody"))
    #     assert_is_measured(span)
    #     assert_span_http_status_code(span, 200)
    #     self.assertEqual(span.service, "test-botocore-tracing.sns")
    #     self.assertEqual(span.resource, "sns.publishbatch")
    #     trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
    #     trace_data_injected = json.loads(trace_json)
    #     self.assertEqual(trace_data_injected[HTTP_HEADER_TRACE_ID], str(span.trace_id))
    #     self.assertEqual(trace_data_injected[HTTP_HEADER_PARENT_ID], str(span.span_id))

    #     # receive message using SQS and ensure headers are present
    #     self.assertEqual(len(response["Messages"]), 1)
    #     msg = response["Messages"][0]
    #     self.assertIsNotNone(msg)
    #     msg_body = json.loads(msg["Body"])
    #     msg_str = msg_body["Message"]
    #     self.assertEqual(msg_str, "test")
    #     msg_attr = msg_body["MessageAttributes"]
    #     self.assertIsNotNone(msg_attr.get("_datadog"))
    #     headers = json.loads(msg_attr["_datadog"]["Value"])
    #     self.assertIsNotNone(headers)
    #     self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
    #     self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
    #     self.assertEqual(len(spans), 2)
    #     self.assertEqual(span.get_tag("aws.region"), "us-east-1")
    #     self.assertEqual(span.get_tag("aws.operation"), "PublishBatch")
    #     self.assertIsNone(span.get_tag("params.MessageBody"))
    #     assert_is_measured(span)
    #     assert_span_http_status_code(span, 200)
    #     self.assertEqual(span.service, "test-botocore-tracing.sns")
    #     self.assertEqual(span.resource, "sns.publishbatch")
    #     trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
    #     trace_data_injected = json.loads(trace_json)
    #     self.assertEqual(trace_data_injected[HTTP_HEADER_TRACE_ID], str(span.trace_id))
    #     self.assertEqual(trace_data_injected[HTTP_HEADER_PARENT_ID], str(span.span_id))

    #     # receive message using SQS and ensure headers are present
    #     self.assertEqual(len(response["Messages"]), 1)
    #     msg = response["Messages"][0]
    #     self.assertIsNotNone(msg)
    #     msg_body = json.loads(msg["Body"])
    #     msg_str = msg_body["Message"]
    #     self.assertEqual(msg_str, "test")
    #     msg_attr = msg_body["MessageAttributes"]
    #     self.assertIsNotNone(msg_attr.get("_datadog"))
    #     headers = json.loads(msg_attr["_datadog"]["Value"])
    #     self.assertIsNotNone(headers)
    #     self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
    #     self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
    #     self.assertEqual(len(spans), 2)
    #     self.assertEqual(span.get_tag("aws.region"), "us-east-1")
    #     self.assertEqual(span.get_tag("aws.operation"), "Publish")
    #     self.assertIsNone(span.get_tag("params.MessageBody"))
    #     assert_is_measured(span)
    #     assert_span_http_status_code(span, 200)
    #     self.assertEqual(span.service, "test-botocore-tracing.sns")
    #     self.assertEqual(span.resource, "sns.publish")
    #     trace_json = span.get_tag("params.MessageAttributes._datadog.StringValue")
    #     self.assertIsNone(trace_json)

    #     # receive message using SQS and ensure headers are present
    #     self.assertEqual(len(response["Messages"]), 1)
    #     msg = response["Messages"][0]
    #     self.assertIsNotNone(msg)
    #     msg_body = json.loads(msg["Body"])
    #     msg_str = msg_body["Message"]
    #     self.assertEqual(msg_str, "test")
    #     msg_attr = msg_body["MessageAttributes"]
    #     self.assertIsNone(msg_attr.get("_datadog"))

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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "PutRecord")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.kinesis")
        self.assertEqual(span.resource, "kinesis.putrecord")
        trace_json = span.get_tag("params.Data")
        self.assertIsNotNone(trace_json)

        headers = json.loads(trace_json)["_datadog"]
        self.assertIsNotNone(headers)
        self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        self.assertEqual(len(resp["Records"]), 1)
        record = resp["Records"][0]
        self.assertIsNotNone(record["Data"])
        data = json.loads(record["Data"])
        headers = data["_datadog"]
        self.assertIsNotNone(headers)
        self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
        print(data)
        print(type(data))

        partition_key = "1234"

        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key)

        # check if the appropriate span was generated
        spans = self.get_spans()
        assert spans
        span = spans[0]
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "PutRecord")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.kinesis")
        self.assertEqual(span.resource, "kinesis.putrecord")
        trace_json = span.get_tag("params.Data")
        self.assertIsNotNone(trace_json)
        print(trace_json)
        print(type(trace_json))

        headers = json.loads(trace_json)["_datadog"]
        self.assertIsNotNone(headers)
        self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        print(resp)
        self.assertEqual(len(resp["Records"]), 1)
        record = resp["Records"][0]
        self.assertIsNotNone(record["Data"])
        print(record["Data"])
        print(record["Data"])
        data = json.loads(record["Data"])
        headers = data["_datadog"]
        self.assertIsNotNone(headers)
        self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
        self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "PutRecords")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.kinesis")
        self.assertEqual(span.resource, "kinesis.putrecords")
        records = span.get_tag("params.Records")
        self.assertIsNotNone(records)
        records = ast.literal_eval(records)
        self.assertEqual(len(records), 2)
        for record in records:
            print(record)
            headers = json.loads(record["Data"])["_datadog"]
            self.assertIsNotNone(headers)
            self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        self.assertEqual(len(resp["Records"]), 2)
        records = resp["Records"]
        for record in records:
            print(record)
            headers = json.loads(record["Data"])["_datadog"]
            self.assertIsNotNone(headers)
            self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

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
        self.assertEqual(len(spans), 1)
        self.assertEqual(span.get_tag("aws.region"), "us-east-1")
        self.assertEqual(span.get_tag("aws.operation"), "PutRecords")
        self.assertIsNone(span.get_tag("params.MessageBody"))
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.service, "test-botocore-tracing.kinesis")
        self.assertEqual(span.resource, "kinesis.putrecords")
        records = span.get_tag("params.Records")
        self.assertIsNotNone(records)
        records = ast.literal_eval(records)
        self.assertEqual(len(records), 2)
        for record in records:
            print(record)
            headers = json.loads(record["Data"])["_datadog"]
            self.assertIsNotNone(headers)
            self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        resp = client.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")
        shard_iterator = resp["ShardIterator"]

        # ensure headers are present in received message
        resp = client.get_records(ShardIterator=shard_iterator)
        self.assertEqual(len(resp["Records"]), 2)
        records = resp["Records"]
        for record in records:
            print(record)
            headers = json.loads(record["Data"])["_datadog"]
            self.assertIsNotNone(headers)
            self.assertEqual(headers[HTTP_HEADER_TRACE_ID], str(span.trace_id))
            self.assertEqual(headers[HTTP_HEADER_PARENT_ID], str(span.span_id))

        client.delete_stream(StreamName=stream_name)
