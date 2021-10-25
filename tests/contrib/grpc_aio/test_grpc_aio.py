import grpc
from grpc import aio
import pytest

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_STACK
from ddtrace.contrib.grpc import constants
from ddtrace.contrib.grpc_aio import patch
from ddtrace.contrib.grpc_aio import unpatch
from tests.contrib.grpc.hello_pb2 import HelloReply
from tests.contrib.grpc.hello_pb2 import HelloRequest
from tests.contrib.grpc.hello_pb2_grpc import HelloServicer
from tests.contrib.grpc.hello_pb2_grpc import HelloStub
from tests.contrib.grpc.hello_pb2_grpc import add_HelloServicer_to_server
from tests.utils import DummyTracer
from tests.utils import assert_is_measured
from tests.utils import override_config


_GRPC_PORT = 50531


class _HelloServicer(HelloServicer):
    async def SayHello(self, request, context):
        if request.name == "propogator":
            metadata = context.invocation_metadata()
            context.set_code(grpc.StatusCode.OK)
            message = ";".join(w.key + "=" + w.value for w in metadata if w.key.startswith("x-datadog"))
            return HelloReply(message=message)

        if request.name == "abort":
            await context.abort(grpc.StatusCode.ABORTED, "aborted")

        if request.name == "exception":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "exception")

        return HelloReply(message="Hello {}".format(request.name))


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    return tracer


@pytest.fixture
def grpc_server(event_loop, tracer):
    """Configures grpc server and starts it in pytest-asyncio event loop"""
    patch()
    Pin.override(constants.GRPC_AIO_PIN_MODULE_SERVER, tracer=tracer)
    _server = aio.server()
    _server.add_insecure_port("[::]:%d" % (_GRPC_PORT))
    add_HelloServicer_to_server(_HelloServicer(), _server)
    event_loop.create_task(_server.start())

    yield

    event_loop.run_until_complete(_server.stop(grace=None))
    tracer.pop()
    unpatch()


def _check_server_span(span, service, method_name, method_kind):
    assert_is_measured(span)
    assert span.name == "grpc"
    assert span.resource == "/helloworld.Hello/{}".format(method_name)
    assert span.service == service
    assert span.error == 0
    assert span.span_type == "grpc"
    assert span.get_tag("grpc.method.path") == "/helloworld.Hello/{}".format(method_name)
    assert span.get_tag("grpc.method.package") == "helloworld"
    assert span.get_tag("grpc.method.service") == "Hello"
    assert span.get_tag("grpc.method.name") == method_name
    assert span.get_tag("grpc.method.kind") == method_kind


@pytest.mark.asyncio
async def test_grpc_server(grpc_server, tracer):
    target = "localhost:%d" % (_GRPC_PORT)
    async with aio.insecure_channel(target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = tracer.writer.spans
    assert len(spans) == 1

    _check_server_span(spans[0], "grpc-aio-server", "SayHello", "unary")


@pytest.mark.asyncio
async def test_pin_not_activated(grpc_server, tracer):
    tracer.configure(enabled=False)
    async with aio.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = tracer.writer.spans
    assert len(spans) == 0


@pytest.mark.asyncio
async def test_analytics_with_rate(grpc_server, tracer):
    with override_config("grpc_aio_server", dict(analytics_enabled=True, analytics_sample_rate=0.75)):
        async with aio.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            await stub.SayHello(HelloRequest(name="test"))

    spans = tracer.writer.spans
    assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.75


@pytest.mark.asyncio
async def test_unary_exception(grpc_server, tracer):
    async with aio.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
        stub = HelloStub(channel)

        with pytest.raises(grpc.RpcError):
            await stub.SayHello(HelloRequest(name="exception"))

    spans = tracer.writer.spans
    server_span = spans[0]

    assert server_span.resource == "/helloworld.Hello/SayHello"
    assert server_span.error == 1
    assert "Traceback" in server_span.get_tag(ERROR_STACK)
    assert "grpc.StatusCode.INVALID_ARGUMENT" in server_span.get_tag(ERROR_STACK)
