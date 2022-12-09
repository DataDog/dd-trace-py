import asyncio
from collections import namedtuple
import sys

import grpc
from grpc import aio
import packaging.version
import pytest

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.grpc import patch
from ddtrace.contrib.grpc import unpatch
from ddtrace.contrib.grpc.patch import GRPC_AIO_PIN_MODULE_CLIENT
from ddtrace.contrib.grpc.patch import GRPC_AIO_PIN_MODULE_SERVER
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

        if request.name == "exception":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        return HelloReply(message="Hello {}".format(request.name))

    async def SayHelloTwice(self, request, context):
        yield HelloReply(message="first response")

        if request.name == "exception":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        yield HelloReply(message="second response")

    async def SayHelloLast(self, request_iterator, context):
        names = []
        async for r in request_iterator:
            if r.name == "exception":
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

            names.append(r.name)

        return HelloReply(message="{}".format(";".join(names)))

    async def SayHelloRepeatedly(self, request_iterator, context):
        async for request in request_iterator:
            if request.name == "exception":
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")
            else:
                yield HelloReply(message=f"Hello {request.name}")
        yield HelloReply(message="Good bye")


class _SyncHelloServicer(HelloServicer):
    @classmethod
    def _assert_not_in_async_context(cls):
        try:
            try:
                asyncio.get_running_loop()
            except AttributeError:
                # Python 3.6 doesn't have `asyncio.get_running_loop()`
                asyncio.get_event_loop()
        except RuntimeError:
            pass
        else:
            assert False, "This method must not invoked in async context"

    def SayHello(self, request, context):
        self._assert_not_in_async_context()

        if request.name == "propogator":
            metadata = context.invocation_metadata()
            context.set_code(grpc.StatusCode.OK)
            message = ";".join(w.key + "=" + w.value for w in metadata if w.key.startswith("x-datadog"))
            return HelloReply(message=message)

        if request.name == "exception":
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        return HelloReply(message="Hello {}".format(request.name))

    def SayHelloTwice(self, request, context):
        self._assert_not_in_async_context()

        yield HelloReply(message="first response")

        if request.name == "exception":
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        yield HelloReply(message="second response")

    def SayHelloLast(self, request_iterator, context):
        self._assert_not_in_async_context()

        names = [r.name for r in list(request_iterator)]

        if "exception" in names:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        return HelloReply(message="{}".format(";".join(names)))

    def SayHelloRepeatedly(self, request_iterator, context):
        self._assert_not_in_async_context()

        for request in request_iterator:
            if request.name == "exception":
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")
            else:
                yield HelloReply(message=f"Hello {request.name}")
        yield HelloReply(message="Good bye")


@pytest.fixture(autouse=True)
def patch_grpc_aio():
    patch()
    yield
    unpatch()


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    Pin.override(GRPC_AIO_PIN_MODULE_CLIENT, tracer=tracer)
    Pin.override(GRPC_AIO_PIN_MODULE_SERVER, tracer=tracer)
    yield tracer
    tracer.pop()


# `pytest_asyncio.fixture` cannot be used
# with pytest-asyncio 0.16.0 which is the latest version available for Python3.6.
@pytest.fixture(params=[_HelloServicer(), _SyncHelloServicer()])
async def server_info(request, tracer, event_loop):
    """Configures grpc server and starts it in pytest-asyncio event loop.
    tracer fixture is imported to make sure the tracer is pinned to the modules.
    """
    _ServerInfo = namedtuple("_ServerInfo", ("target", "abort_supported"))

    _servicer = request.param
    target = f"localhost:{_GRPC_PORT}"
    _server = _create_server(_servicer, target)
    # interceptor can not catch AbortError for sync servicer
    abort_supported = isinstance(_servicer, (_HelloServicer,))

    await _server.start()
    wait_task = event_loop.create_task(_server.wait_for_termination())
    yield _ServerInfo(target, abort_supported)
    await _server.stop(grace=None)
    await wait_task


def _create_server(servicer, target):
    _server = aio.server()
    _server.add_insecure_port(target)
    add_HelloServicer_to_server(servicer, _server)
    return _server


def _get_spans(tracer):
    return tracer._writer.spans


def _check_client_span(span, service, method_name, method_kind):
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
    assert span.get_tag("grpc.status.code") == "StatusCode.OK"
    assert span.get_tag("grpc.host") == "localhost"
    assert span.get_tag("grpc.port") == "50531"


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
async def test_insecure_channel(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    _check_server_span(server_span, "grpc-aio-server", "SayHello", "unary")


@pytest.mark.asyncio
async def test_secure_channel(server_info, tracer):
    credentials = grpc.ChannelCredentials(None)
    async with aio.secure_channel(server_info.target, credentials) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    _check_server_span(server_span, "grpc-aio-server", "SayHello", "unary")


@pytest.mark.asyncio
async def test_invalid_target(server_info, tracer):
    target = "localhost:50051"
    async with aio.insecure_channel(target) as channel:
        stub = HelloStub(channel)
        with pytest.raises(aio.AioRpcError):
            await stub.SayHello(HelloRequest(name="test"))

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 1
    client_span = spans[0]

    assert client_span.resource == "/helloworld.Hello/SayHello"
    assert client_span.error == 1
    assert "failed to connect to all addresses" in client_span.get_tag(ERROR_MSG)
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.UNAVAILABLE"
    assert client_span.get_tag(ERROR_STACK) is None


@pytest.mark.asyncio
async def test_pin_not_activated(server_info, tracer):
    tracer.configure(enabled=False)
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "servicer",
    [_HelloServicer(), _SyncHelloServicer()],
)
async def test_pin_tags_put_in_span(servicer, tracer):
    Pin.override(GRPC_AIO_PIN_MODULE_SERVER, service="server1")
    Pin.override(GRPC_AIO_PIN_MODULE_SERVER, tags={"tag1": "server"})
    target = f"localhost:{_GRPC_PORT}"
    _server = _create_server(servicer, target)
    await _server.start()

    Pin.override(GRPC_AIO_PIN_MODULE_CLIENT, tags={"tag2": "client"})
    async with aio.insecure_channel(target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    await _server.stop(grace=None)

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    assert client_span.get_tag("tag2") == "client"
    _check_server_span(server_span, "server1", "SayHello", "unary")
    assert server_span.get_tag("tag1") == "server"


@pytest.mark.asyncio
async def test_pin_can_be_defined_per_channel(server_info, tracer):
    Pin.override(GRPC_AIO_PIN_MODULE_CLIENT, service="grpc1")
    channel1 = aio.insecure_channel(server_info.target)

    Pin.override(GRPC_AIO_PIN_MODULE_CLIENT, service="grpc2")
    channel2 = aio.insecure_channel(server_info.target)

    stub1 = HelloStub(channel1)
    await stub1.SayHello(HelloRequest(name="test"))
    await channel1.close()

    # DEV: make sure we have two spans before proceeding
    spans = _get_spans(tracer)
    assert len(spans) == 2

    stub2 = HelloStub(channel2)
    await stub2.SayHello(HelloRequest(name="test"))
    await channel2.close()

    spans = _get_spans(tracer)
    assert len(spans) == 4
    client_span1, server_span1, client_span2, server_span2 = spans

    # DEV: Server service default, client services override
    _check_client_span(client_span1, "grpc1", "SayHello", "unary")
    _check_server_span(server_span1, "grpc-aio-server", "SayHello", "unary")
    _check_client_span(client_span2, "grpc2", "SayHello", "unary")
    _check_server_span(server_span2, "grpc-aio-server", "SayHello", "unary")


@pytest.mark.asyncio
async def test_analytics_default(server_info, tracer):
    credentials = grpc.ChannelCredentials(None)
    async with aio.secure_channel(server_info.target, credentials) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    assert client_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None
    _check_server_span(server_span, "grpc-aio-server", "SayHello", "unary")
    assert server_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None


@pytest.mark.asyncio
async def test_analytics_with_rate(server_info, tracer):
    with override_config("grpc_aio_client", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        with override_config("grpc_aio_server", dict(analytics_enabled=True, analytics_sample_rate=0.75)):
            async with aio.insecure_channel(server_info.target) as channel:
                stub = HelloStub(channel)
                await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5
    assert server_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.75


@pytest.mark.asyncio
async def test_priority_sampling(server_info, tracer):
    # DEV: Priority sampling is enabled by default
    # Setting priority sampling reset the writer, we need to re-override it
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        response = await stub.SayHello(HelloRequest(name="propogator"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, _ = spans

    assert "x-datadog-trace-id={}".format(client_span.trace_id) in response.message
    assert "x-datadog-parent-id={}".format(client_span.span_id) in response.message
    assert "x-datadog-sampling-priority=1" in response.message


@pytest.mark.asyncio
async def test_analytics_without_rate(server_info, tracer):
    with override_config("grpc_aio_client", dict(analytics_enabled=True)):
        with override_config("grpc_aio_server", dict(analytics_enabled=True)):
            async with aio.insecure_channel(server_info.target) as channel:
                stub = HelloStub(channel)
                await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    assert client_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0
    _check_server_span(server_span, "grpc-aio-server", "SayHello", "unary")
    assert server_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0


@pytest.mark.skipif(
    sys.version_info >= (3, 11, 0), reason="Segfaults in Python 3.11, see https://github.com/grpc/grpc/issues/31441"
)
@pytest.mark.asyncio
async def test_unary_exception(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)

        with pytest.raises(aio.AioRpcError):
            await stub.SayHello(HelloRequest(name="exception"))

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.resource == "/helloworld.Hello/SayHello"
    assert client_span.error == 1
    assert client_span.get_tag(ERROR_MSG) == "abort_details"
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    assert client_span.get_tag(ERROR_STACK) is None

    assert server_span.resource == "/helloworld.Hello/SayHello"
    if server_info.abort_supported:
        assert server_span.error == 1
        # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
        if packaging.version.parse(grpc.__version__) >= packaging.version.parse("1.38.0-pre1"):
            assert server_span.get_tag(ERROR_MSG) == "abort_details"
            assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        else:
            assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
            assert "AbortError" in server_span.get_tag(ERROR_TYPE)
        assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)


@pytest.mark.skipif(
    sys.version_info >= (3, 11, 0), reason="Segfaults in Python 3.11, see https://github.com/grpc/grpc/issues/31441"
)
@pytest.mark.asyncio
async def test_unary_cancellation(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHello(HelloRequest(name="exception"))
        call.cancel()

    # No span because the call is cancelled before execution.
    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.asyncio
async def test_server_streaming(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        response_counts = 0
        async for response in stub.SayHelloTwice(HelloRequest(name="test")):
            if response_counts == 0:
                assert response.message == "first response"
            elif response_counts == 1:
                assert response.message == "second response"
            response_counts += 1
        assert response_counts == 2

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHelloTwice", "server_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloTwice", "server_streaming")


@pytest.mark.asyncio
async def test_server_streaming_exception(server_info, tracer):
    if not server_info.abort_supported:
        pytest.skip(
            (
                "Skip a test with _SyncHelloServicer "
                "because it often makes the client hang up. "
                "See https://github.com/grpc/grpc/issues/28989."
            )
        )
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        with pytest.raises(aio.AioRpcError):
            async for _ in stub.SayHelloTwice(HelloRequest(name="exception")):
                pass

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.resource == "/helloworld.Hello/SayHelloTwice"
    assert client_span.error == 1
    assert client_span.get_tag(ERROR_MSG) == "abort_details"
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    assert client_span.get_tag(ERROR_STACK) is None

    assert server_span.resource == "/helloworld.Hello/SayHelloTwice"
    assert server_span.error == 1
    # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
    if packaging.version.parse(grpc.__version__) >= packaging.version.parse("1.38.0-pre1"):
        assert server_span.get_tag(ERROR_MSG) == "abort_details"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    else:
        assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
        assert "AbortError" in server_span.get_tag(ERROR_TYPE)
    assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
    assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)


@pytest.mark.asyncio
async def test_server_streaming_cancelled_before_rpc(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloTwice(HelloRequest(name="test"))
        assert call.cancel()
        with pytest.raises(asyncio.CancelledError):
            async for _ in call:
                pass

    # No span because the call is cancelled before execution
    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.asyncio
async def test_server_streaming_cancelled_during_rpc(server_info, tracer):
    if not server_info.abort_supported:
        pytest.skip(
            (
                "Skip a test with _SyncHelloServicer "
                "because it often makes the server termination hang up. "
                "See https://github.com/grpc/grpc/issues/28999."
            )
        )
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloTwice(HelloRequest(name="test"))
        with pytest.raises(asyncio.CancelledError):
            async for _ in call:
                assert call.cancel()

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.resource == "/helloworld.Hello/SayHelloTwice"
    assert client_span.error == 1
    assert client_span.get_tag(ERROR_MSG) == "Locally cancelled by application!"
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.CANCELLED"
    assert client_span.get_tag(ERROR_STACK) is None

    # No error on server end
    _check_server_span(server_span, "grpc-aio-server", "SayHelloTwice", "server_streaming")


@pytest.mark.asyncio
async def test_server_streaming_cancelled_after_rpc(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloTwice(HelloRequest(name="test"))
        response_counts = 0
        async for response in call:
            if response_counts == 0:
                assert response.message == "first response"
            elif response_counts == 1:
                assert response.message == "second response"
            response_counts += 1
        assert response_counts == 2
        assert not call.cancel()

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    # No error because cancelled after execution
    _check_client_span(client_span, "grpc-aio-client", "SayHelloTwice", "server_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloTwice", "server_streaming")


@pytest.mark.asyncio
async def test_client_streaming(server_info, tracer):
    request_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        response = await stub.SayHelloLast(request_iterator)
        assert response.message == "first;second"

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHelloLast", "client_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloLast", "client_streaming")


@pytest.mark.skipif(
    sys.version_info >= (3, 11, 0), reason="Segfaults in Python 3.11, see https://github.com/grpc/grpc/issues/31441"
)
@pytest.mark.asyncio
async def test_client_streaming_exception(server_info, tracer):
    request_iterator = iter(HelloRequest(name=name) for name in ["exception", "test"])
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        with pytest.raises(aio.AioRpcError):
            await stub.SayHelloLast(request_iterator)

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.resource == "/helloworld.Hello/SayHelloLast"
    assert client_span.error == 1
    assert client_span.get_tag(ERROR_MSG) == "abort_details"
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    assert client_span.get_tag(ERROR_STACK) is None

    assert server_span.resource == "/helloworld.Hello/SayHelloLast"
    if server_info.abort_supported:
        assert server_span.error == 1
        # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
        if packaging.version.parse(grpc.__version__) >= packaging.version.parse("1.38.0-pre1"):
            assert server_span.get_tag(ERROR_MSG) == "abort_details"
            assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        else:
            assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
            assert "AbortError" in server_span.get_tag(ERROR_TYPE)
        assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)


@pytest.mark.asyncio
async def test_client_streaming_cancelled_before_rpc(server_info, tracer):
    request_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloLast(request_iterator)
        assert call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    # No span because the call is cancelled before execution
    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.asyncio
async def test_client_streaming_cancelled_after_rpc(server_info, tracer):
    request_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloLast(request_iterator)
        await call
        assert not call.cancel()

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    # No error because cancelled after execution
    _check_client_span(client_span, "grpc-aio-client", "SayHelloLast", "client_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloLast", "client_streaming")


@pytest.mark.asyncio
async def test_bidi_streaming(server_info, tracer):
    names = ["Alice", "Bob"]
    request_iterator = iter(HelloRequest(name=name) for name in names)
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        response_counts = 0
        async for response in stub.SayHelloRepeatedly(request_iterator):
            if response_counts < len(names):
                assert response.message == f"Hello {names[response_counts]}"
            else:
                assert response.message == "Good bye"
            response_counts += 1
        assert response_counts == len(names) + 1

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHelloRepeatedly", "bidi_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloRepeatedly", "bidi_streaming")


@pytest.mark.skipif(
    sys.version_info >= (3, 11, 0), reason="Segfaults in Python 3.11, see https://github.com/grpc/grpc/issues/31441"
)
@pytest.mark.asyncio
async def test_bidi_streaming_exception(server_info, tracer):
    names = ["Alice", "exception", "Bob"]
    request_iterator = iter(HelloRequest(name=name) for name in names)
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        with pytest.raises(aio.AioRpcError):
            async for _ in stub.SayHelloRepeatedly(request_iterator):
                pass

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.resource == "/helloworld.Hello/SayHelloRepeatedly"
    assert client_span.error == 1
    assert client_span.get_tag(ERROR_MSG) == "abort_details"
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    assert client_span.get_tag(ERROR_STACK) is None

    assert server_span.resource == "/helloworld.Hello/SayHelloRepeatedly"
    if server_info.abort_supported:
        assert server_span.error == 1
        # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
        if packaging.version.parse(grpc.__version__) >= packaging.version.parse("1.38.0-pre1"):
            assert server_span.get_tag(ERROR_MSG) == "abort_details"
            assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        else:
            assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
            assert "AbortError" in server_span.get_tag(ERROR_TYPE)
        assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)


@pytest.mark.asyncio
async def test_bidi_streaming_cancelled_before_rpc(server_info, tracer):
    names = ["Alice", "Bob"]
    request_iterator = iter(HelloRequest(name=name) for name in names)
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloRepeatedly(request_iterator)
        assert call.cancel()
        with pytest.raises(asyncio.CancelledError):
            async for response in call:
                pass

    # No span because the call is cancelled before execution
    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.asyncio
async def test_bidi_streaming_cancelled_during_rpc(server_info, tracer):
    if not server_info.abort_supported:
        pytest.skip(
            (
                "Skip a test with _SyncHelloServicer "
                "because it often makes the server termination hang up. "
                "See https://github.com/grpc/grpc/issues/28999."
            )
        )
    names = ["Alice", "Bob"]
    request_iterator = iter(HelloRequest(name=name) for name in names)
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloRepeatedly(request_iterator)
        with pytest.raises(asyncio.CancelledError):
            async for response in call:
                assert call.cancel()
                # NOTE: The server-side RPC is still working right after the client-side RPC is cancelled.
                # Since there is no way to tell whether the server-side RPC is done or not,
                # here it waits for the server-side RPC to be done.
                await asyncio.sleep(0.5)

    await asyncio.sleep(0.5)  # wait for executor pool of AioServer to handle exception
    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    assert client_span.resource == "/helloworld.Hello/SayHelloRepeatedly"
    assert client_span.error == 1
    assert client_span.get_tag(ERROR_MSG) == "Locally cancelled by application!"
    assert client_span.get_tag(ERROR_TYPE) == "StatusCode.CANCELLED"
    assert client_span.get_tag(ERROR_STACK) is None

    # NOTE: The server-side RPC throws `concurrent.futures._base.CancelledError`
    # in old versions of Python, but it's not always so. Thus not checked.
    if sys.version_info >= (3, 8):
        _check_server_span(server_span, "grpc-aio-server", "SayHelloRepeatedly", "bidi_streaming")


@pytest.mark.asyncio
async def test_bidi_streaming_cancelled_after_rpc(server_info, tracer):
    names = ["Alice", "Bob"]
    request_iterator = iter(HelloRequest(name=name) for name in names)
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHelloRepeatedly(request_iterator)
        response_counts = 0
        async for response in call:
            if response_counts < len(names):
                assert response.message == f"Hello {names[response_counts]}"
            else:
                assert response.message == "Good bye"
            response_counts += 1
        assert response_counts == len(names) + 1
        assert not call.cancel()

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    # No error because cancelled after execution
    _check_client_span(client_span, "grpc-aio-client", "SayHelloRepeatedly", "bidi_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloRepeatedly", "bidi_streaming")
