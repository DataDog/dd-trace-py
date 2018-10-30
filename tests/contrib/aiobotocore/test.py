from nose.tools import eq_, ok_, assert_raises
from botocore.errorfactory import ClientError

from ddtrace.contrib.aiobotocore.patch import patch, unpatch

from .utils import aiobotocore_client
from ..asyncio.utils import AsyncioTestCase, mark_asyncio
from ...test_tracer import get_dummy_tracer


class AIOBotocoreTest(AsyncioTestCase):
    """Botocore integration testsuite"""
    def setUp(self):
        super(AIOBotocoreTest, self).setUp()
        patch()
        self.tracer = get_dummy_tracer()

    def tearDown(self):
        super(AIOBotocoreTest, self).tearDown()
        unpatch()
        self.tracer = None

    @mark_asyncio
    def test_double_patch(self):
        # setUp() already patches, patch again and make sure we're idempotent
        patch()
        with aiobotocore_client('ec2', self.tracer) as ec2:
            yield from ec2.describe_instances()

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

    @mark_asyncio
    def test_unpatch(self):
        # setUp() already patches, unpatch and check no spans are generated
        unpatch()
        with aiobotocore_client('ec2', self.tracer) as ec2:
            yield from ec2.describe_instances()

        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)

    @mark_asyncio
    def test_double_unpatch_patch(self):
        unpatch()
        unpatch()
        patch()

        with aiobotocore_client('ec2', self.tracer) as ec2:
            yield from ec2.describe_instances()

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

    @mark_asyncio
    def test_traced_client(self):
        with aiobotocore_client('ec2', self.tracer) as ec2:
            yield from ec2.describe_instances()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]

        eq_(span.get_tag('aws.agent'), 'aiobotocore')
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.get_tag('retry_attempts'), '0')
        eq_(span.service, 'aws.ec2')
        eq_(span.resource, 'ec2.describeinstances')
        eq_(span.name, 'ec2.command')
        eq_(span.span_type, 'http')

    @mark_asyncio
    def test_s3_client(self):
        with aiobotocore_client('s3', self.tracer) as s3:
            yield from s3.list_buckets()
            yield from s3.list_buckets()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 2)
        eq_(len(traces[0]), 1)
        span = traces[0][0]

        eq_(span.get_tag('aws.operation'), 'ListBuckets')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.s3')
        eq_(span.resource, 's3.listbuckets')
        eq_(span.name, 's3.command')

    @mark_asyncio
    def test_s3_client_error(self):
        with aiobotocore_client('s3', self.tracer) as s3:
            with assert_raises(ClientError):
                yield from s3.list_objects(Bucket='mybucket')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]

        eq_(span.resource, 's3.listobjects')
        eq_(span.error, 1)
        ok_('NoSuchBucket' in span.get_tag('error.msg'))

    @mark_asyncio
    def test_s3_client_read(self):
        with aiobotocore_client('s3', self.tracer) as s3:
            # prepare S3 and flush traces if any
            yield from s3.create_bucket(Bucket='tracing')
            yield from s3.put_object(Bucket='tracing', Key='apm', Body=b'')
            self.tracer.writer.pop_traces()
            # calls under test
            response = yield from s3.get_object(Bucket='tracing', Key='apm')
            yield from response['Body'].read()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 2)
        eq_(len(traces[0]), 1)
        eq_(len(traces[1]), 1)

        span = traces[0][0]
        eq_(span.get_tag('aws.operation'), 'GetObject')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.s3')
        eq_(span.resource, 's3.getobject')

        read_span = traces[1][0]
        eq_(read_span.get_tag('aws.operation'), 'GetObject')
        eq_(read_span.get_tag('http.status_code'), '200')
        eq_(read_span.service, 'aws.s3')
        eq_(read_span.resource, 's3.getobject')
        eq_(read_span.name, 's3.command.read')
        # enforce parenting
        eq_(read_span.parent_id, span.span_id)
        eq_(read_span.trace_id, span.trace_id)

    @mark_asyncio
    def test_sqs_client(self):
        with aiobotocore_client('sqs', self.tracer) as sqs:
            yield from sqs.list_queues()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        span = traces[0][0]
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'ListQueues')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.sqs')
        eq_(span.resource, 'sqs.listqueues')

    @mark_asyncio
    def test_kinesis_client(self):
        with aiobotocore_client('kinesis', self.tracer) as kinesis:
            yield from kinesis.list_streams()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        span = traces[0][0]
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'ListStreams')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.kinesis')
        eq_(span.resource, 'kinesis.liststreams')

    @mark_asyncio
    def test_lambda_client(self):
        with aiobotocore_client('lambda', self.tracer) as lambda_client:
            # https://github.com/spulec/moto/issues/906
            yield from lambda_client.list_functions(MaxItems=5)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        span = traces[0][0]
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'ListFunctions')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.lambda')
        eq_(span.resource, 'lambda.listfunctions')

    @mark_asyncio
    def test_kms_client(self):
        with aiobotocore_client('kms', self.tracer) as kms:
            yield from kms.list_keys(Limit=21)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        span = traces[0][0]
        eq_(span.get_tag('aws.region'), 'us-west-2')
        eq_(span.get_tag('aws.operation'), 'ListKeys')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.service, 'aws.kms')
        eq_(span.resource, 'kms.listkeys')
        # checking for protection on STS against security leak
        eq_(span.get_tag('params'), None)

    @mark_asyncio
    def test_unpatch(self):
        unpatch()
        with aiobotocore_client('kinesis', self.tracer) as kinesis:
            yield from kinesis.list_streams()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 0)

    @mark_asyncio
    def test_double_patch(self):
        patch()
        with aiobotocore_client('sqs', self.tracer) as sqs:
            yield from sqs.list_queues()

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

    @mark_asyncio
    def test_opentraced_client(self):
        from tests.opentracer.utils import init_tracer

        ot_tracer = init_tracer('my_svc', self.tracer)

        with ot_tracer.start_active_span('ot_outer_span'):
            with aiobotocore_client('ec2', self.tracer) as ec2:
                    yield from ec2.describe_instances()

        traces = self.tracer.writer.pop_traces()
        print(traces)
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)
        ot_span = traces[0][0]
        dd_span = traces[0][1]

        eq_(ot_span.resource, 'ot_outer_span')
        eq_(ot_span.service, 'my_svc')

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(dd_span.get_tag('aws.agent'), 'aiobotocore')
        eq_(dd_span.get_tag('aws.region'), 'us-west-2')
        eq_(dd_span.get_tag('aws.operation'), 'DescribeInstances')
        eq_(dd_span.get_tag('http.status_code'), '200')
        eq_(dd_span.get_tag('retry_attempts'), '0')
        eq_(dd_span.service, 'aws.ec2')
        eq_(dd_span.resource, 'ec2.describeinstances')
        eq_(dd_span.name, 'ec2.command')

    @mark_asyncio
    def test_opentraced_s3_client(self):
        from tests.opentracer.utils import init_tracer

        ot_tracer = init_tracer('my_svc', self.tracer)

        with ot_tracer.start_active_span('ot_outer_span'):
            with aiobotocore_client('s3', self.tracer) as s3:
                yield from s3.list_buckets()
                with ot_tracer.start_active_span('ot_inner_span1'):
                    yield from s3.list_buckets()
                with ot_tracer.start_active_span('ot_inner_span2'):
                    pass

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 5)
        ot_outer_span = traces[0][0]
        dd_span = traces[0][1]
        ot_inner_span = traces[0][2]
        dd_span2 = traces[0][3]
        ot_inner_span2 = traces[0][4]

        eq_(ot_outer_span.resource, 'ot_outer_span')
        eq_(ot_inner_span.resource, 'ot_inner_span1')
        eq_(ot_inner_span2.resource, 'ot_inner_span2')

        # confirm the parenting
        eq_(ot_outer_span.parent_id, None)
        eq_(dd_span.parent_id, ot_outer_span.span_id)
        eq_(ot_inner_span.parent_id, ot_outer_span.span_id)
        eq_(dd_span2.parent_id, ot_inner_span.span_id)
        eq_(ot_inner_span2.parent_id, ot_outer_span.span_id)

        eq_(dd_span.get_tag('aws.operation'), 'ListBuckets')
        eq_(dd_span.get_tag('http.status_code'), '200')
        eq_(dd_span.service, 'aws.s3')
        eq_(dd_span.resource, 's3.listbuckets')
        eq_(dd_span.name, 's3.command')

        eq_(dd_span2.get_tag('aws.operation'), 'ListBuckets')
        eq_(dd_span2.get_tag('http.status_code'), '200')
        eq_(dd_span2.service, 'aws.s3')
        eq_(dd_span2.resource, 's3.listbuckets')
        eq_(dd_span2.name, 's3.command')
