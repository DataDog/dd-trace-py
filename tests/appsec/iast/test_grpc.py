import threading
import time

import grpc
from grpc.framework.foundation import logging_pool
import pytest
import six

from ddtrace import Pin
from ddtrace._trace.span import _get_64_highest_order_bits_as_hex
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.grpc import constants
from ddtrace.contrib.grpc import patch
from ddtrace.contrib.grpc import unpatch
from ddtrace.contrib.grpc.patch import _unpatch_server
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase, override_env
from tests.utils import snapshot

from tests.appsec.iast.fixtures.grpc.api_pb2 import (HelloRequest, ApiRequest, MapTest, MapRequest, MsgMapRequest,
                                                        MsgMapTest)
from tests.appsec.iast.fixtures.grpc.api_pb2_grpc import ApiStub, ApiServicer, add_ApiServicer_to_server
from tests.appsec.iast.fixtures.grpc.api import ApiServer, ApiClient


_GRPC_PORT = 50531

class GrpcTestCase(TracerTestCase):
    def setUp(self):
        super(GrpcTestCase, self).setUp()
        patch()
        Pin.override(constants.GRPC_PIN_MODULE_SERVER, tracer=self.tracer)
        Pin.override(constants.GRPC_PIN_MODULE_CLIENT, tracer=self.tracer)
        self._start_server()

    def tearDown(self):
        self._stop_server()
        # Remove any remaining spans
        self.tracer.pop()
        # Unpatch grpc
        unpatch()
        super(GrpcTestCase, self).tearDown()

    def _start_server(self):
        self._server_pool = logging_pool.pool(1)
        self._server = grpc.server(self._server_pool)
        self._server.add_insecure_port("[::]:%d" % (_GRPC_PORT))
        add_ApiServicer_to_server(ApiServer(), self._server)
        self._server.start()

    def _stop_server(self):
        self._server.stop(None)
        self._server_pool.shutdown(wait=True)

    def test_insecure_channel_using_args_parameter(self):
        def insecure_channel_using_args(target):
            return grpc.insecure_channel(target)

        self._test_insecure_channel(insecure_channel_using_args)

    def test_insecure_channel_using_kwargs_parameter(self):
        def insecure_channel_using_kwargs(target):
            return grpc.insecure_channel(target=target)

        self._test_insecure_channel(insecure_channel_using_kwargs)

    def _test_insecure_channel(self, insecure_channel_function):
        target = "localhost:%d" % (_GRPC_PORT)
        with insecure_channel_function(target) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        self._check_client_span(client_span, "grpc-client", "SayHello", "unary")
        self._check_server_span(server_span, "grpc-server", "SayHello", "unary")

    def test_secure_channel_using_args_parameter(self):
        def secure_channel_using_args(target, **kwargs):
            return grpc.secure_channel(target, **kwargs)

        self._test_secure_channel(secure_channel_using_args)

    def test_secure_channel_using_kwargs_parameter(self):
        def secure_channel_using_kwargs(target, **kwargs):
            return grpc.secure_channel(target=target, **kwargs)

        self._test_secure_channel(secure_channel_using_kwargs)

    def _test_secure_channel(self, secure_channel_function):
        target = "localhost:%d" % (_GRPC_PORT)
        with secure_channel_function(target, credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        self._check_client_span(client_span, "grpc-client", "SayHello", "unary")
        self._check_server_span(server_span, "grpc-server", "SayHello", "unary")

    def test_pin_not_activated(self):
        self.tracer.configure(enabled=False)
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert()
        assert len(spans) == 0

    def test_pin_tags_are_put_in_span(self):
        # DEV: stop and restart server to catch overridden pin
        self._stop_server()
        Pin.override(constants.GRPC_PIN_MODULE_SERVER, service="server1")
        Pin.override(constants.GRPC_PIN_MODULE_SERVER, tags={"tag1": "server"})
        Pin.override(constants.GRPC_PIN_MODULE_CLIENT, tags={"tag2": "client"})
        self._start_server()
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        assert spans[1].service == "server1"
        assert spans[1].get_tag("tag1") == "server"
        assert spans[0].get_tag("tag2") == "client"


    def test_pin_can_be_defined_per_channel(self):
        Pin.override(constants.GRPC_PIN_MODULE_CLIENT, service="grpc1")
        channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))

        Pin.override(constants.GRPC_PIN_MODULE_CLIENT, service="grpc2")
        channel2 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))

        stub1 = HelloStub(channel1)
        stub1.SayHello(HelloRequest(name="test"))
        channel1.close()

        # DEV: make sure we have two spans before proceeding
        spans = self.get_spans_with_sync_and_assert(size=2)

        stub2 = HelloStub(channel2)
        stub2.SayHello(HelloRequest(name="test"))
        channel2.close()

        spans = self.get_spans_with_sync_and_assert(size=4)

        # DEV: Server service default, client services override
        self._check_server_span(spans[1], "grpc-server", "SayHello", "unary")
        self._check_client_span(spans[0], "grpc1", "SayHello", "unary")
        self._check_server_span(spans[3], "grpc-server", "SayHello", "unary")
        self._check_client_span(spans[2], "grpc2", "SayHello", "unary")

    def test_analytics_default(self):
        with grpc.secure_channel("localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None
        assert spans[1].get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_analytics_with_rate(self):
        with self.override_config("grpc_server", dict(analytics_enabled=True, analytics_sample_rate=0.75)):
            with self.override_config("grpc", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
                with grpc.secure_channel(
                    "localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)
                ) as channel:
                    stub = HelloStub(channel)
                    stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        assert spans[1].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.75
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    def test_analytics_without_rate(self):
        with self.override_config("grpc_server", dict(analytics_enabled=True)):
            with self.override_config("grpc", dict(analytics_enabled=True)):
                with grpc.secure_channel(
                    "localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)
                ) as channel:
                    stub = HelloStub(channel)
                    stub.SayHello(HelloRequest(name="test"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0
        assert spans[1].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0

    def test_server_stream(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            responses_iterator = stub.SayHelloTwice(HelloRequest(name="test"))
            responses_iterator.add_done_callback(callback)
            assert len(list(responses_iterator)) == 2
            callback_called.wait(timeout=1)

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        self._check_client_span(client_span, "grpc-client", "SayHelloTwice", "server_streaming")
        self._check_server_span(server_span, "grpc-server", "SayHelloTwice", "server_streaming")

    def test_server_stream_once(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            responses_iterator = stub.SayHelloTwice(HelloRequest(name="once"))
            responses_iterator.add_done_callback(callback)
            response = six.next(responses_iterator)
            callback_called.wait(timeout=1)
            assert response.message == "first response"

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        self._check_client_span(client_span, "grpc-client", "SayHelloTwice", "server_streaming")
        self._check_server_span(server_span, "grpc-server", "SayHelloTwice", "server_streaming")

    def test_client_stream(self):
        requests_iterator = iter(HelloRequest(name=name) for name in ["first", "second"])

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHelloLast(requests_iterator)
            assert response.message == "first;second"

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        self._check_client_span(client_span, "grpc-client", "SayHelloLast", "client_streaming")
        self._check_server_span(server_span, "grpc-server", "SayHelloLast", "client_streaming")

    def test_bidi_stream(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        requests_iterator = iter(HelloRequest(name=name) for name in ["first", "second", "third", "fourth", "fifth"])

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            responses_iterator = stub.SayHelloRepeatedly(requests_iterator)
            responses_iterator.add_done_callback(callback)
            messages = [r.message for r in responses_iterator]
            callback_called.wait(timeout=1)
            assert list(messages) == ["first;second", "third;fourth", "fifth"]

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        self._check_client_span(client_span, "grpc-client", "SayHelloRepeatedly", "bidi_streaming")
        self._check_server_span(server_span, "grpc-server", "SayHelloRepeatedly", "bidi_streaming")

    def test_priority_sampling(self):
        # DEV: Priority sampling is enabled by default
        # Setting priority sampling reset the writer, we need to re-override it

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name="propogator"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        assert f"x-datadog-trace-id={str(client_span._trace_id_64bits)}" in response.message
        assert f"_dd.p.tid={_get_64_highest_order_bits_as_hex(client_span.trace_id)}" in response.message
        assert "x-datadog-parent-id={}".format(client_span.span_id) in response.message
        assert "x-datadog-sampling-priority=1" in response.message

    def test_unary_abort(self):
        with grpc.secure_channel("localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(grpc.RpcError):
                stub.SayHello(HelloRequest(name="abort"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        assert client_span.resource == "/helloworld.Hello/SayHello"
        assert client_span.error == 1
        assert client_span.get_tag(ERROR_MSG) == "aborted"
        assert client_span.get_tag(ERROR_TYPE) == "StatusCode.ABORTED"
        assert client_span.get_tag("grpc.status.code") == "StatusCode.ABORTED"
        assert client_span.get_tag("component") == "grpc"
        assert client_span.get_tag("span.kind") == "client"

    def test_custom_interceptor_exception(self):
        # add an interceptor that raises a custom exception and check error tags
        # are added to spans
        raise_exception_interceptor = _RaiseExceptionClientInterceptor()
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            with self.assertRaises(_CustomException):
                intercept_channel = grpc.intercept_channel(channel, raise_exception_interceptor)
                stub = HelloStub(intercept_channel)
                stub.SayHello(HelloRequest(name="custom-exception"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        assert client_span.resource == "/helloworld.Hello/SayHello"
        assert client_span.error == 1
        assert client_span.get_tag(ERROR_MSG) == "custom"
        assert client_span.get_tag(ERROR_TYPE) == "tests.contrib.grpc.test_grpc._CustomException"
        assert client_span.get_tag(ERROR_STACK) is not None
        assert client_span.get_tag("grpc.status.code") == "StatusCode.INTERNAL"
        assert client_span.get_tag("component") == "grpc"
        assert client_span.get_tag("span.kind") == "client"

        # no exception on server end
        assert server_span.resource == "/helloworld.Hello/SayHello"
        assert server_span.error == 0
        assert server_span.get_tag(ERROR_MSG) is None
        assert server_span.get_tag(ERROR_TYPE) is None
        assert server_span.get_tag(ERROR_STACK) is None
        assert server_span.get_tag("component") == "grpc_server"
        assert server_span.get_tag("span.kind") == "server"

    def test_client_cancellation(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        # unpatch and restart server since we are only testing here caller cancellation
        self._stop_server()
        _unpatch_server()
        self._start_server()

        # have servicer sleep whenever request is handled to ensure we can cancel before server responds
        # to requests
        requests_iterator = iter(HelloRequest(name=name) for name in ["sleep"])

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            with self.assertRaises(grpc.RpcError):
                stub = HelloStub(channel)
                responses_iterator = stub.SayHelloRepeatedly(requests_iterator)
                responses_iterator.add_done_callback(callback)
                responses_iterator.cancel()
                next(responses_iterator)
            callback_called.wait(timeout=1)

        spans = self.get_spans_with_sync_and_assert(size=1)
        client_span = spans[0]

        assert client_span.resource == "/helloworld.Hello/SayHelloRepeatedly"
        assert client_span.error == 1
        assert client_span.get_tag(ERROR_MSG) == "Locally cancelled by application!"
        assert client_span.get_tag(ERROR_TYPE) == "StatusCode.CANCELLED"
        assert client_span.get_tag(ERROR_STACK) is None
        assert client_span.get_tag("grpc.status.code") == "StatusCode.CANCELLED"
        assert client_span.get_tag("component") == "grpc"
        assert client_span.get_tag("span.kind") == "client"

    def test_unary_exception(self):
        with grpc.secure_channel("localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(grpc.RpcError):
                stub.SayHello(HelloRequest(name="exception"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        assert client_span.resource == "/helloworld.Hello/SayHello"
        assert client_span.error == 1
        assert client_span.get_tag(ERROR_MSG) == "exception"
        assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        assert client_span.get_tag("grpc.status.code") == "StatusCode.INVALID_ARGUMENT"
        assert client_span.get_tag("component") == "grpc"
        assert client_span.get_tag("span.kind") == "client"

        assert server_span.resource == "/helloworld.Hello/SayHello"
        assert server_span.error == 1
        assert server_span.get_tag(ERROR_MSG) == "exception"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        assert server_span.get_tag("component") == "grpc_server"
        assert server_span.get_tag("span.kind") == "server"
        assert "Traceback" in server_span.get_tag(ERROR_STACK)
        assert "grpc.StatusCode.INVALID_ARGUMENT" in server_span.get_tag(ERROR_STACK)

    def test_client_stream_exception(self):
        requests_iterator = iter(HelloRequest(name=name) for name in ["first", "exception"])

        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(grpc.RpcError):
                stub.SayHelloLast(requests_iterator)

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        assert client_span.resource == "/helloworld.Hello/SayHelloLast"
        assert client_span.error == 1
        assert client_span.get_tag(ERROR_MSG) == "exception"
        assert client_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        assert client_span.get_tag("grpc.status.code") == "StatusCode.INVALID_ARGUMENT"
        assert client_span.get_tag("component") == "grpc"
        assert client_span.get_tag("span.kind") == "client"

        assert server_span.resource == "/helloworld.Hello/SayHelloLast"
        assert server_span.error == 1
        assert server_span.get_tag(ERROR_MSG) == "exception"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
        assert server_span.get_tag("component") == "grpc_server"
        assert server_span.get_tag("span.kind") == "server"
        assert "Traceback" in server_span.get_tag(ERROR_STACK)
        assert "grpc.StatusCode.INVALID_ARGUMENT" in server_span.get_tag(ERROR_STACK)

    def test_server_stream_exception(self):
        # use an event to signal when the callbacks have been called from the response
        callback_called = threading.Event()

        def callback(response):
            callback_called.set()

        with grpc.secure_channel("localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(grpc.RpcError):
                responses_iterator = stub.SayHelloTwice(HelloRequest(name="exception"))
                responses_iterator.add_done_callback(callback)
                list(responses_iterator)
            callback_called.wait(timeout=1)

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans

        assert client_span.resource == "/helloworld.Hello/SayHelloTwice"
        assert client_span.error == 1
        assert client_span.get_tag(ERROR_MSG) == "exception"
        assert client_span.get_tag(ERROR_TYPE) == "StatusCode.RESOURCE_EXHAUSTED"
        assert client_span.get_tag("grpc.status.code") == "StatusCode.RESOURCE_EXHAUSTED"
        assert client_span.get_tag("span.kind") == "client"

        assert server_span.resource == "/helloworld.Hello/SayHelloTwice"
        assert server_span.error == 1
        assert server_span.get_tag(ERROR_MSG) == "exception"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.RESOURCE_EXHAUSTED"
        assert server_span.get_tag("span.kind") == "server"
        assert "Traceback" in server_span.get_tag(ERROR_STACK)
        assert "grpc.StatusCode.RESOURCE_EXHAUSTED" in server_span.get_tag(ERROR_STACK)

    def test_unknown_servicer(self):
        with grpc.secure_channel("localhost:%d" % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(grpc.RpcError) as exception_context:
                stub.SayHelloUnknown(HelloRequest(name="unknown"))
            rpc_error = exception_context.exception
            assert grpc.StatusCode.UNIMPLEMENTED == rpc_error.code()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_app_service_name(self):
        """
        When a service name is specified by the user
            It should be used for grpc server spans
            It should be included in grpc client spans
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
        stub1 = HelloStub(channel1)
        stub1.SayHello(HelloRequest(name="test"))
        channel1.close()

        # DEV: make sure we have two spans before proceeding
        spans = self.get_spans_with_sync_and_assert(size=2)

        self._check_server_span(spans[1], "mysvc", "SayHello", "unary")
        self._check_client_span(spans[0], "grpc-client", "SayHello", "unary")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_IAST_ENABLED="1"))
    def test_jjj_iast(self):
        # DEV: stop and restart server to catch overridden pin
        with override_env({"DD_IAST_ENABLED": "True"}):
            with self.override_config("grpc", dict(service_name="myclientsvc")):
                with self.override_config("grpc_server", dict(service_name="myserversvc")):
                    channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                    stub1 = HelloStub(channel1)
                    stub1.SayHello(HelloRequest(name="test"))
                    channel1.close()

                spans = self.get_spans_with_sync_and_assert(size=2)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_name_config_override(self):
        """
        When a service name is specified by the user in config.grpc{_server}
            It should be used in grpc client spans
            It should be used in grpc server spans
        """
        with self.override_config("grpc", dict(service_name="myclientsvc")):
            with self.override_config("grpc_server", dict(service_name="myserversvc")):
                channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
                stub1 = HelloStub(channel1)
                stub1.SayHello(HelloRequest(name="test"))
                channel1.close()

        spans = self.get_spans_with_sync_and_assert(size=2)

        self._check_server_span(spans[1], "myserversvc", "SayHello", "unary")
        self._check_client_span(spans[0], "myclientsvc", "SayHello", "unary")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_GRPC_SERVICE="myclientsvc"))
    def test_client_service_name_config_env_override(self):
        """
        When a service name is specified by the user in the DD_GRPC_SERVICE env var
            It should be used in grpc client spans
        """
        channel1 = grpc.insecure_channel("localhost:%d" % (_GRPC_PORT))
        stub1 = HelloStub(channel1)
        stub1.SayHello(HelloRequest(name="test"))
        channel1.close()

        spans = self.get_spans_with_sync_and_assert(size=2)
        self._check_client_span(spans[0], "myclientsvc", "SayHello", "unary")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0(self):
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="propogator"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        assert client_span.name == "grpc", "Expected 'grpc', got %s" % client_span.name
        assert server_span.name == "grpc", "Expected 'grpc', got %s" % server_span.name

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1(self):
        with grpc.insecure_channel("localhost:%d" % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name="propogator"))

        spans = self.get_spans_with_sync_and_assert(size=2)
        client_span, server_span = spans
        assert client_span.name == "grpc.client.request", "Expected 'grpc.client.request', got %s" % client_span.name
        assert server_span.name == "grpc.server.request", "Expected 'grpc.server.request', got %s" % server_span.name

@pytest.fixture()
def patch_grpc():
    patch()
    try:
        yield
    finally:
        unpatch()


@snapshot(ignores=["meta.network.destination.port"], wait_for_num_traces=2)
def test_method_service(patch_grpc):
    def handler(request, context):
        return b""

    server = grpc.server(
        logging_pool.pool(1),
        options=(("grpc.so_reuseport", 0),),
    )
    port = server.add_insecure_port("[::]:0")
    channel = grpc.insecure_channel("[::]:{}".format(port))
    server.add_generic_rpc_handlers((_UnaryUnaryRpcHandler(handler),))
    try:
        server.start()
        channel.unary_unary("/Servicer/Handler")(b"request")
        channel.unary_unary("/pkg.Servicer/Handler")(b"request")
    finally:
        server.stop(None)
