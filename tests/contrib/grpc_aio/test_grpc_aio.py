import asyncio
from collections import namedtuple
import os
import sys

import grpc
from grpc import aio
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.grpc.patch import GRPC_AIO_PIN_MODULE_CLIENT
from ddtrace.contrib.internal.grpc.patch import GRPC_AIO_PIN_MODULE_SERVER
from ddtrace.contrib.internal.grpc.patch import patch
from ddtrace.contrib.internal.grpc.patch import unpatch
from ddtrace.contrib.internal.grpc.utils import _parse_rpc_repr_string
from ddtrace.trace import Pin
import ddtrace.vendor.packaging.version as packaging_version
from tests.contrib.grpc.hello_pb2 import HelloReply
from tests.contrib.grpc.hello_pb2 import HelloRequest
from tests.contrib.grpc.hello_pb2_grpc import HelloServicer
from tests.contrib.grpc.hello_pb2_grpc import HelloStub
from tests.contrib.grpc.hello_pb2_grpc import add_HelloServicer_to_server
from tests.contrib.grpc_aio.hellostreamingworld_pb2 import HelloReply as HelloReplyStream
from tests.contrib.grpc_aio.hellostreamingworld_pb2 import HelloRequest as HelloRequestStream
from tests.contrib.grpc_aio.hellostreamingworld_pb2_grpc import MultiGreeterServicer
from tests.contrib.grpc_aio.hellostreamingworld_pb2_grpc import MultiGreeterStub
from tests.contrib.grpc_aio.hellostreamingworld_pb2_grpc import add_MultiGreeterServicer_to_server
from tests.utils import DummyTracer
from tests.utils import assert_is_measured


_GRPC_PORT = 50531
NUMBER_OF_REPLY = 10


class _CoroHelloServicer(HelloServicer):
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
        await context.write(HelloReply(message="first response"))

        if request.name == "exception":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        await context.write(HelloReply(message="second response"))

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
                await context.write(HelloReply(message=f"Hello {request.name}"))
        await context.write(HelloReply(message="Good bye"))


class _AsyncGenHelloServicer(HelloServicer):
    async def SayHelloTwice(self, request, context):
        # Read/Write API can be used together with yield statements.
        await context.write(HelloReply(message="first response"))

        if request.name == "exception":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "abort_details")

        yield HelloReply(message="second response")

    async def SayHelloRepeatedly(self, request_iterator, context):
        async for request in request_iterator:
            if request.name == "exception":
                # Read/Write API can be used together with yield statements.
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
            raise AssertionError("This method must not invoked in async context")

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


class Greeter(MultiGreeterServicer):
    async def sayHello(self, request, context):
        for i in range(NUMBER_OF_REPLY):
            yield HelloReplyStream(message=f"Hello number {i}, {request.name}!")


class DummyClientInterceptor(aio.UnaryUnaryClientInterceptor):
    async def intercept_unary_unary(self, continuation, client_call_details, request):
        undone_call = await continuation(client_call_details, request)
        return await undone_call

    def add_done_callback(self, unused_callback):
        pass


@pytest.fixture(autouse=True)
def patch_grpc_aio():
    patch()
    yield
    unpatch()


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    Pin._override(GRPC_AIO_PIN_MODULE_CLIENT, tracer=tracer)
    Pin._override(GRPC_AIO_PIN_MODULE_SERVER, tracer=tracer)
    yield tracer
    tracer.pop()


@pytest.fixture
async def async_server_info(request, tracer, event_loop):
    _ServerInfo = namedtuple("_ServerInfo", ("target", "abort_supported"))
    _server = grpc.aio.server()
    add_MultiGreeterServicer_to_server(Greeter(), _server)
    _servicer = request.param
    target = f"localhost:{_GRPC_PORT}"
    _server.add_insecure_port(target)
    # interceptor can not catch AbortError for sync servicer
    abort_supported = not isinstance(_servicer, (_SyncHelloServicer,))

    await _server.start()
    wait_task = event_loop.create_task(_server.wait_for_termination())
    yield _ServerInfo(target, abort_supported)
    await _server.stop(grace=None)
    await wait_task


# `pytest_asyncio.fixture` cannot be used
# with pytest-asyncio 0.16.0 which is the latest version available for Python3.6.
@pytest.fixture
async def server_info(request, tracer, event_loop):
    """Configures grpc server and starts it in pytest-asyncio event loop.
    tracer fixture is imported to make sure the tracer is pinned to the modules.
    """
    _ServerInfo = namedtuple("_ServerInfo", ("target", "abort_supported"))
    _servicer = request.param
    target = f"localhost:{_GRPC_PORT}"
    _server = _create_server(_servicer, target)
    # interceptor can not catch AbortError for sync servicer
    abort_supported = not isinstance(_servicer, (_SyncHelloServicer,))

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


def _check_client_span(span, service, method_name, method_kind, resource="helloworld.Hello"):
    assert_is_measured(span)
    assert span.name == "grpc"
    assert span.resource == "/{}/{}".format(resource, method_name)
    assert span.service == service
    assert span.error == 0
    assert span.span_type == "grpc"
    assert span.get_tag("grpc.method.path") == "/{}/{}".format(resource, method_name)
    assert span.get_tag("grpc.method.package") == resource.split(".")[0]
    assert span.get_tag("grpc.method.service") == resource.split(".")[1]
    assert span.get_tag("grpc.method.name") == method_name
    assert span.get_tag("grpc.method.kind") == method_kind
    assert span.get_tag("grpc.status.code") == "StatusCode.OK"
    assert span.get_tag("grpc.host") == "localhost"
    assert span.get_tag("peer.hostname") == "localhost"
    assert span.get_tag("network.destination.port") == "50531"
    assert span.get_tag("component") == "grpc_aio_client"
    assert span.get_tag("span.kind") == "client"


def _check_server_span(span, service, method_name, method_kind, resource="helloworld.Hello"):
    assert_is_measured(span)
    assert span.name == "grpc"
    assert span.resource == "/{}/{}".format(resource, method_name)
    assert span.service == service
    assert span.error == 0
    assert span.span_type == "grpc"
    assert span.get_tag("grpc.method.path") == "/{}/{}".format(resource, method_name)
    assert span.get_tag("grpc.method.package") == resource.split(".")[0]
    assert span.get_tag("grpc.method.service") == resource.split(".")[1]
    assert span.get_tag("grpc.method.name") == method_name
    assert span.get_tag("grpc.method.kind") == method_kind
    assert span.get_tag("component") == "grpc_aio_server"
    assert span.get_tag("span.kind") == "server"


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
async def test_insecure_channel(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    _check_server_span(server_span, "grpc-aio-server", "SayHello", "unary")


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
async def test_secure_channel_with_interceptor_in_args(server_info, tracer):
    credentials = grpc.ChannelCredentials(None)
    interceptors = [DummyClientInterceptor()]
    async with aio.secure_channel(server_info.target, credentials, None, None, interceptors) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    _check_server_span(server_span, "grpc-aio-server", "SayHello", "unary")


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
async def test_pin_not_activated(server_info, tracer):
    tracer.enabled = False
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.parametrize(
    "servicer",
    [_CoroHelloServicer(), _SyncHelloServicer()],
)
async def test_pin_tags_put_in_span(servicer, tracer):
    Pin._override(GRPC_AIO_PIN_MODULE_SERVER, service="server1")
    Pin._override(GRPC_AIO_PIN_MODULE_SERVER, tags={"tag1": "server"})
    target = f"localhost:{_GRPC_PORT}"
    _server = _create_server(servicer, target)
    await _server.start()

    Pin._override(GRPC_AIO_PIN_MODULE_CLIENT, tags={"tag2": "client"})
    async with aio.insecure_channel(target) as channel:
        stub = HelloStub(channel)
        await stub.SayHello(HelloRequest(name="test"))

    await _server.stop(grace=None)

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    _check_client_span(client_span, "grpc-aio-client", "SayHello", "unary")
    assert client_span.get_tag("tag2") == "client"
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"
    _check_server_span(server_span, "server1", "SayHello", "unary")
    assert server_span.get_tag("tag1") == "server"
    assert server_span.get_tag("component") == "grpc_aio_server"
    assert server_span.get_tag("span.kind") == "server"


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
async def test_pin_can_be_defined_per_channel(server_info, tracer):
    Pin._override(GRPC_AIO_PIN_MODULE_CLIENT, service="grpc1")
    channel1 = aio.insecure_channel(server_info.target)

    Pin._override(GRPC_AIO_PIN_MODULE_CLIENT, service="grpc2")
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


@pytest.mark.skipif(
    sys.version_info >= (3, 11, 0),
    reason="Segfaults in Python 3.11.0 and later",
)
@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"

    assert server_span.resource == "/helloworld.Hello/SayHello"
    if server_info.abort_supported:
        assert server_span.error == 1
        # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
        if packaging_version.parse(grpc.__version__) >= packaging_version.parse("1.38.0-pre1"):
            assert server_span.get_tag(ERROR_MSG) == "abort_details"
            assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        else:
            assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
            assert "AbortError" in server_span.get_tag(ERROR_TYPE)
        assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag("component") == "grpc_aio_server"
        assert server_span.get_tag("span.kind") == "server"


@pytest.mark.skipif(
    sys.version_info >= (3, 11, 0),
    reason="Segfaults in Python 3.11.0 and later",
)
@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
async def test_unary_cancellation(server_info, tracer):
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        call = stub.SayHello(HelloRequest(name="exception"))
        call.cancel()

    # No span because the call is cancelled before execution.
    spans = _get_spans(tracer)
    assert len(spans) == 0


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"

    assert server_span.resource == "/helloworld.Hello/SayHelloTwice"
    assert server_span.error == 1
    # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
    if packaging_version.parse(grpc.__version__) >= packaging_version.parse("1.38.0-pre1"):
        assert server_span.get_tag(ERROR_MSG) == "abort_details"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
    else:
        assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
        assert "AbortError" in server_span.get_tag(ERROR_TYPE)
    assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
    assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)
    assert server_span.get_tag("component") == "grpc_aio_server"
    assert server_span.get_tag("span.kind") == "server"


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"

    # No error on server end
    _check_server_span(server_span, "grpc-aio-server", "SayHelloTwice", "server_streaming")


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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
    sys.version_info >= (3, 11, 0),
    reason="Segfaults in Python 3.11.0 and later",
)
@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"
    assert client_span.get_tag("peer.hostname") == "localhost"

    assert server_span.resource == "/helloworld.Hello/SayHelloLast"
    if server_info.abort_supported:
        assert server_span.error == 1
        # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
        if packaging_version.parse(grpc.__version__) >= packaging_version.parse("1.38.0-pre1"):
            assert server_span.get_tag(ERROR_MSG) == "abort_details"
            assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        else:
            assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
            assert "AbortError" in server_span.get_tag(ERROR_TYPE)
        assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag("component") == "grpc_aio_server"
        assert server_span.get_tag("span.kind") == "server"


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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


@pytest.mark.parametrize("server_info", [_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
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


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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
    sys.version_info >= (3, 11, 0),
    reason="Segfaults in Python 3.11.0 and later",
)
@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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
    error_msg = client_span.get_tag(ERROR_MSG)
    assert error_msg in ("abort_details", "Internal error from Core")
    if error_msg == "abort_details":
        assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        assert client_span.get_tag(ERROR_STACK) is None
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"

    assert server_span.resource == "/helloworld.Hello/SayHelloRepeatedly"
    if server_info.abort_supported:
        assert server_span.error == 1
        # grpc provide servicer_context.details and servicer_context.code above 1.38.0-pre1
        if packaging_version.parse(grpc.__version__) >= packaging_version.parse("1.38.0-pre1"):
            assert server_span.get_tag(ERROR_MSG) == "abort_details"
            assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        else:
            assert server_span.get_tag(ERROR_MSG) == "Locally aborted."
            assert "AbortError" in server_span.get_tag(ERROR_TYPE)
        assert server_span.get_tag(ERROR_MSG) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag(ERROR_TYPE) in server_span.get_tag(ERROR_STACK)
        assert server_span.get_tag("component") == "grpc_aio_server"
        assert server_span.get_tag("span.kind") == "server"


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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
    assert client_span.get_tag("peer.hostname") == "localhost"
    assert client_span.get_tag("component") == "grpc_aio_client"
    assert client_span.get_tag("span.kind") == "client"

    # NOTE: The server-side RPC throws `concurrent.futures._base.CancelledError`
    # in old versions of Python, but it's not always so. Thus not checked.
    _check_server_span(server_span, "grpc-aio-server", "SayHelloRepeatedly", "bidi_streaming")


@pytest.mark.parametrize(
    "server_info", [_CoroHelloServicer(), _AsyncGenHelloServicer(), _SyncHelloServicer()], indirect=True
)
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


@pytest.mark.parametrize(
    "service_schema",
    [
        (None, None),
        (None, "v0"),
        (None, "v1"),
        ("mysvc", None),
        ("mysvc", "v0"),
        ("mysvc", "v1"),
    ],
)
def test_schematization_of_operation(ddtrace_run_python_code_in_subprocess, service_schema):
    service, schema = service_schema
    expected_operation_name_format = {
        None: ("grpc"),
        "v0": ("grpc"),
        "v1": ("grpc.{}.request"),
    }[schema]
    code = """
import sys

import pytest
from grpc import aio

from tests.contrib.grpc.hello_pb2 import HelloReply
from tests.contrib.grpc.hello_pb2 import HelloRequest
from tests.contrib.grpc.hello_pb2_grpc import HelloServicer
from tests.contrib.grpc.hello_pb2_grpc import HelloStub
from tests.contrib.grpc_aio.test_grpc_aio import _CoroHelloServicer
from tests.contrib.grpc_aio.test_grpc_aio import _SyncHelloServicer
from tests.contrib.grpc_aio.test_grpc_aio import _get_spans
from tests.contrib.grpc_aio.test_grpc_aio import patch_grpc_aio
from tests.contrib.grpc_aio.test_grpc_aio import server_info
from tests.contrib.grpc_aio.test_grpc_aio import tracer

@pytest.mark.parametrize("server_info",[_CoroHelloServicer(), _SyncHelloServicer()], indirect=True)
async def test_client_streaming(server_info, tracer):
    request_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])
    async with aio.insecure_channel(server_info.target) as channel:
        stub = HelloStub(channel)
        response = await stub.SayHelloLast(request_iterator)
        assert response.message == "first;second"

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    operation_name_format = "{}"
    assert client_span.name == operation_name_format.format("client")
    assert server_span.name == operation_name_format.format("server")

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__, "--asyncio-mode=auto"]))
    """.format(
        expected_operation_name_format
    )
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


class StreamInterceptor(grpc.aio.UnaryStreamClientInterceptor):
    async def intercept_unary_stream(self, continuation, call_details, request):
        response_iterator = await continuation(call_details, request)
        return response_iterator


async def run_streaming_example(server_info, use_generator=False):
    i = 0
    async with grpc.aio.insecure_channel(server_info.target, interceptors=[StreamInterceptor()]) as channel:
        stub = MultiGreeterStub(channel)

        # Read from an async generator
        if use_generator:
            async for response in stub.sayHello(HelloRequestStream(name="you")):
                assert response.message == "Hello number {}, you!".format(i)
                i += 1

        # Direct read from the stub
        else:
            hello_stream = stub.sayHello(HelloRequestStream(name="will"))
            while True:
                response = await hello_stream.read()
                if response == grpc.aio.EOF:
                    break
                assert response.message == "Hello number {}, will!".format(i)
                i += 1


@pytest.mark.skip(
    "Bug/error from grpc when adding an async streaming client interceptor throws StopAsyncIteration. Issue can be \
    found at: https://github.com/DataDog/dd-trace-py/issues/9139"
)
@pytest.mark.parametrize("async_server_info", [_CoroHelloServicer()], indirect=True)
async def test_async_streaming_direct_read(async_server_info, tracer):
    await run_streaming_example(async_server_info)

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    # No error because cancelled after execution
    _check_client_span(client_span, "grpc-aio-client", "SayHelloRepeatedly", "bidi_streaming")
    _check_server_span(server_span, "grpc-aio-server", "SayHelloRepeatedly", "bidi_streaming")


@pytest.mark.parametrize("async_server_info", [_CoroHelloServicer()], indirect=True)
async def test_async_streaming_generator(async_server_info, tracer):
    await run_streaming_example(async_server_info, use_generator=True)

    spans = _get_spans(tracer)
    assert len(spans) == 2
    client_span, server_span = spans

    # No error because cancelled after execution
    _check_client_span(
        client_span, "grpc-aio-client", "sayHello", "server_streaming", "hellostreamingworld.MultiGreeter"
    )
    _check_server_span(
        server_span, "grpc-aio-server", "sayHello", "server_streaming", "hellostreamingworld.MultiGreeter"
    )


repr_test_cases = [
    {
        "rpc_string": 'status = StatusCode.OK, details = "Everything is fine"',
        "expected_code": grpc.StatusCode.OK,
        "expected_details": "Everything is fine",
        "expect_error": False,
    },
    {
        "rpc_string": 'status = StatusCode.ABORTED, details = "Everything is not fine"',
        "expected_code": grpc.StatusCode.ABORTED,
        "expected_details": "Everything is not fine",
        "expect_error": False,
    },
    {
        "rpc_string": "status = , details = ",
        "expected_code": None,
        "expected_details": None,
        "expect_error": True,
    },
    {
        "rpc_string": 'details = "Everything is fine"',
        "expected_code": None,
        "expected_details": None,
        "expect_error": True,
    },
    {
        "rpc_string": "status = StatusCode.ABORTED",
        "expected_code": None,
        "expected_details": None,
        "expect_error": True,
    },
    {
        "rpc_string": 'status = StatusCode.INVALID_STATUS_CODE_SAD, details = "Everything is fine"',
        "expected_code": None,
        "expected_details": None,
        "expect_error": True,
    },
    {
        "rpc_string": '  status = StatusCode.CANCELLED  , details =  "Everything is not fine"  ',
        "expected_code": grpc.StatusCode.CANCELLED,
        "expected_details": "Everything is not fine",
        "expect_error": False,
    },
    {
        "rpc_string": "status=StatusCode.OK details='Everything is fine'",
        "expected_code": None,
        "expected_details": None,
        "expect_error": True,
    },
]


@pytest.mark.parametrize("case", repr_test_cases)
def test_parse_rpc_repr_string(case):
    try:
        code, details = _parse_rpc_repr_string(case["rpc_string"], grpc)
        assert not case[
            "expect_error"
        ], f"Test case with repr string: {case['rpc_string']} expected error but got result"
        assert (
            code == case["expected_code"]
        ), f"Test case with repr string: {case['rpc_string']} expected code {case['expected_code']} but got {code}"
        assert details == case["expected_details"], (
            f"Test case with repr string: {case['rpc_string']} expected details {case['expected_details']} but"
            f"got {details}"
        )
    except ValueError as e:
        assert case[
            "expect_error"
        ], f"Test case with repr string: {case['rpc_string']} did not expect error but got {e}"
