import aiobotocore
from botocore.errorfactory import ClientError
import pytest

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.aiobotocore.patch import patch
from ddtrace.contrib.aiobotocore.patch import unpatch
from ddtrace.internal.compat import stringify
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code
from tests.utils import override_config
from tests.utils import override_global_config

from .utils import aiobotocore_client


@pytest.fixture(autouse=True)
def patch_aiobotocore():
    patch()
    yield
    unpatch()


@pytest.mark.asyncio
async def test_traced_client(tracer):
    async with aiobotocore_client("ec2", tracer) as ec2:
        await ec2.describe_instances()

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.agent") == "aiobotocore"
    assert span.get_tag("aws.region") == "us-west-2"
    assert span.get_tag("aws.operation") == "DescribeInstances"
    assert_span_http_status_code(span, 200)
    assert span.get_metric("retry_attempts") == 0
    assert span.service == "aws.ec2"
    assert span.resource == "ec2.describeinstances"
    assert span.name == "ec2.command"
    assert span.span_type == "http"
    assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None


@pytest.mark.asyncio
async def test_traced_client_analytics(tracer):
    with override_config("aiobotocore", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        async with aiobotocore_client("ec2", tracer) as ec2:
            await ec2.describe_instances()

    traces = tracer.pop_traces()
    assert traces
    span = traces[0][0]
    assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5


@pytest.mark.asyncio
async def test_s3_client(tracer):
    async with aiobotocore_client("s3", tracer) as s3:
        await s3.list_buckets()
        await s3.list_buckets()

    traces = tracer.pop_traces()
    assert len(traces) == 2
    assert len(traces[0]) == 1
    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.operation") == "ListBuckets"
    assert_span_http_status_code(span, 200)
    assert span.service == "aws.s3"
    assert span.resource == "s3.listbuckets"
    assert span.name == "s3.command"


@pytest.mark.asyncio
async def test_s3_put(tracer):
    params = dict(Key="foo", Bucket="mybucket", Body=b"bar")

    async with aiobotocore_client("s3", tracer) as s3:
        await s3.create_bucket(Bucket="mybucket")
        await s3.put_object(**params)

    spans = [trace[0] for trace in tracer.pop_traces()]
    assert spans
    assert len(spans) == 2
    assert spans[0].get_tag("aws.operation") == "CreateBucket"

    assert_is_measured(spans[0])
    assert_span_http_status_code(spans[0], 200)
    assert spans[0].service == "aws.s3"
    assert spans[0].resource == "s3.createbucket"

    assert_is_measured(spans[1])
    assert spans[1].get_tag("aws.operation") == "PutObject"
    assert spans[1].resource == "s3.putobject"
    assert spans[1].get_tag("params.Key") == stringify(params["Key"])
    assert spans[1].get_tag("params.Bucket") == stringify(params["Bucket"])
    assert spans[1].get_tag("params.Body") is None


@pytest.mark.asyncio
async def test_s3_client_error(tracer):
    async with aiobotocore_client("s3", tracer) as s3:
        with pytest.raises(ClientError):
            # FIXME: add proper clean-up to tearDown
            await s3.list_objects(Bucket="doesnotexist")

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]

    assert_is_measured(span)
    assert span.resource == "s3.listobjects"
    assert span.error == 1
    assert "NoSuchBucket" in span.get_tag("error.msg")


@pytest.mark.asyncio
async def test_s3_client_read(tracer):
    async with aiobotocore_client("s3", tracer) as s3:
        # prepare S3 and flush traces if any
        await s3.create_bucket(Bucket="tracing")
        await s3.put_object(Bucket="tracing", Key="apm", Body=b"")
        tracer.pop_traces()
        # calls under test
        response = await s3.get_object(Bucket="tracing", Key="apm")
        await response["Body"].read()

    traces = tracer.pop_traces()
    version = aiobotocore.__version__.split(".")
    pre_08 = int(version[0]) == 0 and int(version[1]) < 8
    if pre_08:
        assert len(traces) == 2
        assert len(traces[1]) == 1
    else:
        assert len(traces) == 1

    assert len(traces[0]) == 1

    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.operation") == "GetObject"
    assert_span_http_status_code(span, 200)
    assert span.service == "aws.s3"
    assert span.resource == "s3.getobject"

    if pre_08:
        read_span = traces[1][0]
        assert read_span.get_tag("aws.operation") == "GetObject"
        assert_span_http_status_code(read_span, 200)
        assert read_span.service == "aws.s3"
        assert read_span.resource == "s3.getobject"
        assert read_span.name == "s3.command.read"
        # enforce parenting
        assert read_span.parent_id == span.span_id
        assert read_span.trace_id == span.trace_id


@pytest.mark.asyncio
async def test_sqs_client(tracer):
    async with aiobotocore_client("sqs", tracer) as sqs:
        await sqs.list_queues()

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1

    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.region") == "us-west-2"
    assert span.get_tag("aws.operation") == "ListQueues"
    assert_span_http_status_code(span, 200)
    assert span.service == "aws.sqs"
    assert span.resource == "sqs.listqueues"


@pytest.mark.asyncio
async def test_kinesis_client(tracer):
    async with aiobotocore_client("kinesis", tracer) as kinesis:
        await kinesis.list_streams()

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1

    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.region") == "us-west-2"
    assert span.get_tag("aws.operation") == "ListStreams"
    assert_span_http_status_code(span, 200)
    assert span.service == "aws.kinesis"
    assert span.resource == "kinesis.liststreams"


@pytest.mark.asyncio
async def test_lambda_client(tracer):
    async with aiobotocore_client("lambda", tracer) as lambda_client:
        # https://github.com/spulec/moto/issues/906
        await lambda_client.list_functions(MaxItems=5)

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1

    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.region") == "us-west-2"
    assert span.get_tag("aws.operation") == "ListFunctions"
    assert_span_http_status_code(span, 200)
    assert span.service == "aws.lambda"
    assert span.resource == "lambda.listfunctions"


@pytest.mark.asyncio
async def test_kms_client(tracer):
    async with aiobotocore_client("kms", tracer) as kms:
        await kms.list_keys(Limit=21)

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1

    span = traces[0][0]

    assert_is_measured(span)
    assert span.get_tag("aws.region") == "us-west-2"
    assert span.get_tag("aws.operation") == "ListKeys"
    assert_span_http_status_code(span, 200)
    assert span.service == "aws.kms"
    assert span.resource == "kms.listkeys"
    # checking for protection on STS against security leak
    assert span.get_tag("params") is None


@pytest.mark.asyncio
async def test_unpatch(tracer):
    unpatch()
    async with aiobotocore_client("kinesis", tracer) as kinesis:
        await kinesis.list_streams()

    traces = tracer.pop_traces()
    assert len(traces) == 0


@pytest.mark.asyncio
async def test_double_patch(tracer):
    patch()
    async with aiobotocore_client("sqs", tracer) as sqs:
        await sqs.list_queues()

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1


@pytest.mark.asyncio
async def test_opentraced_client(tracer):
    from tests.opentracer.utils import init_tracer

    ot_tracer = init_tracer("my_svc", tracer)

    with ot_tracer.start_active_span("ot_outer_span"):
        async with aiobotocore_client("ec2", tracer) as ec2:
            await ec2.describe_instances()

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 2
    ot_span = traces[0][0]
    dd_span = traces[0][1]

    assert ot_span.resource == "ot_outer_span"
    assert ot_span.service == "my_svc"

    # confirm the parenting
    assert ot_span.parent_id is None
    assert dd_span.parent_id == ot_span.span_id

    assert_is_measured(dd_span)
    assert dd_span.get_tag("aws.agent") == "aiobotocore"
    assert dd_span.get_tag("aws.region") == "us-west-2"
    assert dd_span.get_tag("aws.operation") == "DescribeInstances"
    assert_span_http_status_code(dd_span, 200)
    assert dd_span.get_metric("retry_attempts") == 0
    assert dd_span.service == "aws.ec2"
    assert dd_span.resource == "ec2.describeinstances"
    assert dd_span.name == "ec2.command"


@pytest.mark.asyncio
async def test_opentraced_s3_client(tracer):
    from tests.opentracer.utils import init_tracer

    ot_tracer = init_tracer("my_svc", tracer)

    with ot_tracer.start_active_span("ot_outer_span"):
        async with aiobotocore_client("s3", tracer) as s3:
            await s3.list_buckets()
            with ot_tracer.start_active_span("ot_inner_span1"):
                await s3.list_buckets()
            with ot_tracer.start_active_span("ot_inner_span2"):
                pass

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 5
    ot_outer_span = traces[0][0]
    dd_span = traces[0][1]
    ot_inner_span = traces[0][2]
    dd_span2 = traces[0][3]
    ot_inner_span2 = traces[0][4]

    assert ot_outer_span.resource == "ot_outer_span"
    assert ot_inner_span.resource == "ot_inner_span1"
    assert ot_inner_span2.resource == "ot_inner_span2"

    # confirm the parenting
    assert ot_outer_span.parent_id is None
    assert dd_span.parent_id == ot_outer_span.span_id
    assert ot_inner_span.parent_id == ot_outer_span.span_id
    assert dd_span2.parent_id == ot_inner_span.span_id
    assert ot_inner_span2.parent_id == ot_outer_span.span_id

    assert_is_measured(dd_span)
    assert dd_span.get_tag("aws.operation") == "ListBuckets"
    assert_span_http_status_code(dd_span, 200)
    assert dd_span.service == "aws.s3"
    assert dd_span.resource == "s3.listbuckets"
    assert dd_span.name == "s3.command"

    assert dd_span2.get_tag("aws.operation") == "ListBuckets"
    assert_span_http_status_code(dd_span2, 200)
    assert dd_span2.service == "aws.s3"
    assert dd_span2.resource == "s3.listbuckets"
    assert dd_span2.name == "s3.command"


@pytest.mark.asyncio
async def test_user_specified_service(tracer):
    """
    When a service name is specified by the user
        The aiobotocore integration should use it as the service name
    """
    with override_global_config(dict(service="mysvc")):
        # Repatch to take config into account
        unpatch()
        patch()
        async with aiobotocore_client("ec2", tracer) as ec2:
            await ec2.describe_instances()

        traces = tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert span.service == "mysvc"


@pytest.mark.asyncio
async def test_response_context_manager(tracer):
    # the client should call the wrapped __aenter__ and return the
    # object proxy
    async with aiobotocore_client("s3", tracer) as s3:
        # prepare S3 and flush traces if any
        await s3.create_bucket(Bucket="tracing")
        await s3.put_object(Bucket="tracing", Key="apm", Body=b"")
        tracer.pop_traces()
        # `async with` under test
        response = await s3.get_object(Bucket="tracing", Key="apm")
        async with response["Body"] as stream:
            await stream.read()

    traces = tracer.pop_traces()

    version = aiobotocore.__version__.split(".")
    pre_08 = int(version[0]) == 0 and int(version[1]) < 8
    # Version 0.8+ generates only one span for reading an object.
    if pre_08:
        assert len(traces) == 2
        assert len(traces[0]) == 1
        assert len(traces[1]) == 1

        span = traces[0][0]
        assert_is_measured(span)
        assert span.get_tag("aws.operation") == "GetObject"
        assert_span_http_status_code(span, 200)
        assert span.service == "aws.s3"
        assert span.resource == "s3.getobject"

        read_span = traces[1][0]
        assert_is_measured(read_span)
        assert read_span.get_tag("aws.operation") == "GetObject"
        assert_span_http_status_code(read_span, 200)
        assert read_span.service == "aws.s3"
        assert read_span.resource == "s3.getobject"
        assert read_span.name == "s3.command.read"
        # enforce parenting
        assert read_span.parent_id == span.span_id
        assert read_span.trace_id == span.trace_id
    else:
        assert len(traces[0]) == 1
        assert len(traces[0]) == 1

        span = traces[0][0]
        assert_is_measured(span)
        assert span.get_tag("aws.operation") == "GetObject"
        assert_span_http_status_code(span, 200)
        assert span.service == "aws.s3"
        assert span.resource == "s3.getobject"
        assert span.name == "s3.command"
