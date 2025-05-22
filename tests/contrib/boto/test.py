# 3p
# testing
from unittest import skipUnless

import boto.awslambda
import boto.ec2
import boto.elasticache
import boto.kms
import boto.s3
import boto.sqs
import boto.sts
from moto import mock_ec2
from moto import mock_lambda
from moto import mock_s3
from moto import mock_sts

from ddtrace.contrib.internal.boto.patch import patch
from ddtrace.contrib.internal.boto.patch import unpatch
from ddtrace.ext import http
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME

# project
from ddtrace.trace import Pin
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code


class BotoTest(TracerTestCase):
    """Botocore integration testsuite"""

    TEST_SERVICE = "test-boto-tracing"

    def setUp(self):
        super(BotoTest, self).setUp()
        patch()

    @mock_ec2
    def test_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(ec2)

        ec2.get_all_instances()
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.get_tag("aws.operation"), "DescribeInstances")
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_tag(http.METHOD), "POST")
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("region"), "us-west-2")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.get_tag("aws.operation"), "RunInstances")
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_tag(http.METHOD), "POST")
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("region"), "us-west-2")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")
        self.assertEqual(span.service, "test-boto-tracing.ec2")
        self.assertEqual(span.resource, "ec2.runinstances")
        self.assertEqual(span.name, "ec2.command")
        self.assertEqual(span.span_type, "http")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_env_service_default_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.ec2")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_env_service_v0_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.ec2")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_env_service_v1_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "mysvc")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_default_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.ec2")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_v0_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.ec2")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_v1_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, DEFAULT_SPAN_SERVICE_NAME)

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.name, "ec2.command")

    @mock_ec2
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1_ec2_client(self):
        ec2 = boto.ec2.connect_to_region("us-west-2")
        Pin.get_from(ec2)._clone(tracer=self.tracer).onto(ec2)

        # Create an instance
        ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.name, "aws.ec2.request")

    def _test_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(s3)

        s3.get_all_buckets()
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_tag(http.METHOD), "GET")
        self.assertEqual(span.get_tag("aws.operation"), "get_all_buckets")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")

        # Create a bucket command
        s3.create_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        create_span = spans[0]
        assert_is_measured(create_span)
        assert_span_http_status_code(create_span, 200)
        self.assertEqual(create_span.get_tag(http.METHOD), "PUT")
        self.assertEqual(create_span.get_tag("aws.operation"), "create_bucket")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_tag(http.METHOD), "HEAD")
        self.assertEqual(span.get_tag("aws.operation"), "head_bucket")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")
        self.assertEqual(span.service, "test-boto-tracing.s3")
        self.assertEqual(span.resource, "s3.head")
        self.assertEqual(span.name, "s3.command")

        # Checking for resource in case of error
        try:
            s3.get_bucket("big_bucket")
        except Exception:
            spans = self.pop_spans()
            assert spans
            span = spans[0]
            self.assertEqual(span.resource, "s3.head")

        return create_span

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_env_service_name_default_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.s3")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_env_service_name_v0_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.s3")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_env_service_name_v1_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "mysvc")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.name, "s3.command")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.name, "aws.s3.request")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_name_default_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.s3")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_name_v0_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, "aws.s3")

    @mock_s3
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_name_v1_s3_client(self):
        # DEV: To test tag params check create bucket's span
        s3 = boto.s3.connect_to_region("us-east-1")
        Pin.get_from(s3)._clone(tracer=self.tracer).onto(s3)

        # Create the test bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()

        # Get the created bucket
        s3.get_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.service, DEFAULT_SPAN_SERVICE_NAME)

    @mock_s3
    def test_s3_client(self):
        span = self._test_s3_client()
        # DEV: Not currently supported
        self.assertIsNone(span.get_tag("aws.s3.bucket_name"))
        self.assertIsNone(span.get_tag("bucketname"))

    @mock_s3
    def test_s3_client_no_params(self):
        with self.override_config("boto", dict(tag_no_params=True)):
            span = self._test_s3_client()
            self.assertIsNone(span.get_tag("aws.s3.bucket_name"))
            self.assertIsNone(span.get_tag("bucketname"))

    @mock_s3
    def test_s3_put(self):
        s3 = boto.s3.connect_to_region("us-east-1")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(s3)
        s3.create_bucket("mybucket")
        bucket = s3.get_bucket("mybucket")
        k = boto.s3.key.Key(bucket)
        k.key = "foo"
        k.set_contents_from_string("bar")

        spans = self.pop_spans()
        assert spans
        # create bucket
        self.assertEqual(len(spans), 3)
        self.assertEqual(spans[0].get_tag("aws.operation"), "create_bucket")
        self.assertEqual(spans[0].get_tag("component"), "boto")
        self.assertEqual(spans[0].get_tag("span.kind"), "client")
        assert_is_measured(spans[0])
        assert_span_http_status_code(spans[0], 200)
        self.assertEqual(spans[0].service, "test-boto-tracing.s3")
        self.assertEqual(spans[0].resource, "s3.put")
        # get bucket
        assert_is_measured(spans[1])
        self.assertEqual(spans[1].get_tag("aws.operation"), "head_bucket")
        self.assertEqual(spans[1].get_tag("component"), "boto")
        self.assertEqual(spans[1].get_tag("span.kind"), "client")
        self.assertEqual(spans[1].resource, "s3.head")
        # put object
        assert_is_measured(spans[2])
        self.assertEqual(spans[2].get_tag("aws.operation"), "_send_file_internal")
        self.assertEqual(spans[2].get_tag("component"), "boto")
        self.assertEqual(spans[2].get_tag("span.kind"), "client")
        self.assertEqual(spans[2].resource, "s3.put")

    @mock_lambda
    def test_unpatch(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(lamb)
        unpatch()

        # multiple calls
        lamb.list_functions()
        spans = self.pop_spans()
        assert not spans, spans

    @mock_s3
    def test_double_patch(self):
        s3 = boto.s3.connect_to_region("us-east-1")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(s3)

        patch()
        patch()
        # Get the created bucket
        s3.create_bucket("cheese")
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 1)

    @mock_lambda
    def test_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(lamb)

        # multiple calls
        lamb.list_functions()
        lamb.list_functions()

        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 2)
        span = spans[0]
        assert_is_measured(span)
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_tag(http.METHOD), "GET")
        self.assertEqual(span.get_tag("aws.region"), "us-east-2")
        self.assertEqual(span.get_tag("region"), "us-east-2")
        self.assertEqual(span.get_tag("aws.operation"), "list_functions")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")
        self.assertEqual(span.service, "test-boto-tracing.lambda")
        self.assertEqual(span.resource, "lambda.get")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_env_service_name_default_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, "aws.lambda")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_env_service_name_v0_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, "aws.lambda")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_env_service_name_v1_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, "mysvc")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_unspecified_service_name_default_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, "aws.lambda")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_name_v0_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, "aws.lambda")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_name_v1_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, DEFAULT_SPAN_SERVICE_NAME)

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.name, "lambda.command")

    @mock_lambda
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1_lambda_client(self):
        lamb = boto.awslambda.connect_to_region("us-east-2")
        Pin.get_from(lamb)._clone(tracer=self.tracer).onto(lamb)

        lamb.list_functions()

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.name, "aws.lambda.request")

    @mock_sts
    def test_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("region"), "us-west-2")
        self.assertEqual(span.get_tag("aws.operation"), "GetFederationToken")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")
        self.assertEqual(span.service, "test-boto-tracing.sts")
        self.assertEqual(span.resource, "sts.getfederationtoken")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_env_default_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "aws.sts")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_env_v0_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "aws.sts")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_env_v1_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "mysvc")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_default_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "aws.sts")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_v0_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "aws.sts")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, DEFAULT_SPAN_SERVICE_NAME)

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.name, "sts.command")

    @mock_sts
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_sts_client(self):
        sts = boto.sts.connect_to_region("us-west-2")
        Pin.get_from(sts)._clone(tracer=self.tracer).onto(sts)

        sts.get_federation_token(12, duration=10)

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.name, "aws.sts.request")

    @skipUnless(
        False,
        (
            "Test to reproduce the case where args sent to patched function are None,"
            "can't be mocked: needs AWS credentials"
        ),
    )
    def test_elasticache_client(self):
        elasticache = boto.elasticache.connect_to_region("us-west-2")
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(elasticache)

        elasticache.describe_cache_clusters()

        spans = self.pop_spans()
        assert spans
        span = spans[0]
        self.assertEqual(span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(span.get_tag("region"), "us-west-2")
        self.assertEqual(span.get_tag("component"), "boto")
        self.assertEqual(span.get_tag("span.kind"), "client")
        self.assertEqual(span.service, "test-boto-tracing.elasticache")
        self.assertEqual(span.resource, "elasticache")

    @mock_ec2
    def test_ec2_client_ot(self):
        """OpenTracing compatibility check of the test_ec2_client test."""
        ec2 = boto.ec2.connect_to_region("us-west-2")
        ot_tracer = init_tracer("my_svc", self.tracer)
        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(ec2)

        with ot_tracer.start_active_span("ot_span"):
            ec2.get_all_instances()
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        self.assertIsNone(ot_span.parent_id)
        self.assertEqual(dd_span.parent_id, ot_span.span_id)

        self.assertEqual(ot_span.resource, "ot_span")
        self.assertEqual(dd_span.get_tag("aws.operation"), "DescribeInstances")
        self.assertEqual(dd_span.get_tag("component"), "boto")
        self.assertEqual(dd_span.get_tag("span.kind"), "client")
        assert_span_http_status_code(dd_span, 200)
        self.assertEqual(dd_span.get_tag(http.METHOD), "POST")
        self.assertEqual(dd_span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(dd_span.get_tag("region"), "us-west-2")

        with ot_tracer.start_active_span("ot_span"):
            ec2.run_instances(21)
        spans = self.pop_spans()
        assert spans
        self.assertEqual(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        self.assertIsNone(ot_span.parent_id)
        self.assertEqual(dd_span.parent_id, ot_span.span_id)

        self.assertEqual(dd_span.get_tag("aws.operation"), "RunInstances")
        assert_span_http_status_code(dd_span, 200)
        self.assertEqual(dd_span.get_tag(http.METHOD), "POST")
        self.assertEqual(dd_span.get_tag("aws.region"), "us-west-2")
        self.assertEqual(dd_span.get_tag("region"), "us-west-2")
        self.assertEqual(dd_span.get_tag("component"), "boto")
        self.assertEqual(dd_span.get_tag("span.kind"), "client")
        self.assertEqual(dd_span.service, "test-boto-tracing.ec2")
        self.assertEqual(dd_span.resource, "ec2.runinstances")
        self.assertEqual(dd_span.name, "ec2.command")
