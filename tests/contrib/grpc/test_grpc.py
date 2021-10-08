import threading
import time

import grpc
from grpc._grpcio_metadata import __version__ as _GRPC_VERSION
from grpc.framework.foundation import logging_pool
import pytest
import six

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.grpc import constants
from ddtrace.contrib.grpc import patch
from ddtrace.contrib.grpc import unpatch
from ddtrace.contrib.grpc.patch import _unpatch_server
from tests.utils import TracerTestCase
from tests.utils import snapshot

from .hello_pb2 import HelloReply
from .hello_pb2 import HelloRequest
from .hello_pb2_grpc import HelloServicer
from .hello_pb2_grpc import HelloStub
from .hello_pb2_grpc import add_HelloServicer_to_server


_GRPC_PORT = 50531
_GRPC_VERSION = tuple([int(i) for i in _GRPC_VERSION.split(".")])


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

    def get_spans_with_sync_and_assert(self, size=0, retry=20):
        # testing instrumentation with grpcio < 1.14.0 presents a problem for
        # checking spans written to the dummy tracer
        # see https://github.com/grpc/grpc/issues/14621

        spans = super(GrpcTestCase, self).get_spans()

        if _GRPC_VERSION >= (1, 14):
            assert len(spans) == size
            return spans

        for _ in range(retry):
            if len(spans) == size:
                assert len(spans) == size
                return spans
            time.sleep(0.1)

        return spans

    def _start_server(self):
        self._server_pool = logging_pool.pool(1)
        self._server = grpc.server(self._server_pool)
        self._server.add_insecure_port("[::]:%d" % (_GRPC_PORT))
        add_HelloServicer_to_server(_HelloServicer(), self._server)
        self._server.start()

    def _stop_server(self):
        self._server.stop(None)
        self._server_pool.shutdown(wait=True)

    def _check_client_span(self, span, service, method_name, method_kind):
        self.assert_is_measured(span)
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

    def _check_server_span(self, span, service, method_name, method_kind):
        self.assert_is_measured(span)
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

        assert "x-datadog-trace-id={}".format(client_span.trace_id) in response.message
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

        # no exception on server end
        assert server_span.resource == "/helloworld.Hello/SayHello"
        assert server_span.error == 0
        assert server_span.get_tag(ERROR_MSG) is None
        assert server_span.get_tag(ERROR_TYPE) is None
        assert server_span.get_tag(ERROR_STACK) is None

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

        assert server_span.resource == "/helloworld.Hello/SayHello"
        assert server_span.error == 1
        assert server_span.get_tag(ERROR_MSG) == "exception"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
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

        assert server_span.resource == "/helloworld.Hello/SayHelloLast"
        assert server_span.error == 1
        assert server_span.get_tag(ERROR_MSG) == "exception"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.INVALID_ARGUMENT"
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

        assert server_span.resource == "/helloworld.Hello/SayHelloTwice"
        assert server_span.error == 1
        assert server_span.get_tag(ERROR_MSG) == "exception"
        assert server_span.get_tag(ERROR_TYPE) == "StatusCode.RESOURCE_EXHAUSTED"
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


class _HelloServicer(HelloServicer):
    def SayHello(self, request, context):
        if request.name == "propogator":
            metadata = context.invocation_metadata()
            context.set_code(grpc.StatusCode.OK)
            message = ";".join(w.key + "=" + w.value for w in metadata if w.key.startswith("x-datadog"))
            return HelloReply(message=message)

        if request.name == "abort":
            context.abort(grpc.StatusCode.ABORTED, "aborted")

        if request.name == "exception":
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "exception")

        return HelloReply(message="Hello {}".format(request.name))

    def SayHelloTwice(self, request, context):
        yield HelloReply(message="first response")

        if request.name == "exception":
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "exception")

        if request.name == "once":
            # Mimic behavior of scenario where only one result is expected from
            # streaming response and the RPC is successfully terminated, as is
            # the case with grpc_helpers._StreamingResponseIterator in the
            # Google API Library wraps a _MultiThreadedRendezvous future. An
            # example of this iterator only called once is in the Google Cloud
            # Firestore library.
            # https://github.com/googleapis/python-api-core/blob/f87bccbfda11d2c2d1a2ddb6611c1209e29289d9/google/api_core/grpc_helpers.py#L80-L116
            # https://github.com/googleapis/python-firestore/blob/e57258c51e4b4aa664cc927454056412756fc7ac/google/cloud/firestore_v1/document.py#L400-L404
            return

        yield HelloReply(message="secondresponse")

    def SayHelloLast(self, request_iterator, context):
        names = [r.name for r in list(request_iterator)]

        if "exception" in names:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "exception")

        return HelloReply(message="{}".format(";".join(names)))

    def SayHelloRepeatedly(self, request_iterator, context):
        last_request = None
        for request in request_iterator:
            if last_request is not None:
                yield HelloReply(message="{}".format(";".join([last_request.name, request.name])))
                last_request = None
            else:
                last_request = request

        # response for dangling request
        if last_request is not None:
            yield HelloReply(message="{}".format(last_request.name))

    def SayHelloUnknown(self, request, context):
        yield HelloReply(message="unknown")


class _CustomException(Exception):
    pass


class _RaiseExceptionClientInterceptor(grpc.UnaryUnaryClientInterceptor):
    def _intercept_call(self, continuation, client_call_details, request_or_iterator):
        # allow computation to complete
        continuation(client_call_details, request_or_iterator).result()

        raise _CustomException("custom")

    def intercept_unary_unary(self, continuation, client_call_details, request):
        return self._intercept_call(continuation, client_call_details, request)


def test_handle_response_future_like():
    from ddtrace.contrib.grpc.client_interceptor import _handle_response
    from ddtrace.span import Span

    span = Span(None, None)

    def finish_span():
        span.finish()

    class FutureLike(object):
        def add_done_callback(self, fn):
            finish_span()

    class NotFutureLike(object):
        pass

    _handle_response(span, NotFutureLike())
    assert span.duration is None
    _handle_response(span, FutureLike())
    assert span.duration is not None


@pytest.fixture()
def patch_grpc():
    patch()
    try:
        yield
    finally:
        unpatch()


class _UnaryUnaryRpcHandler(grpc.GenericRpcHandler):
    def __init__(self, handler):
        self._handler = handler

    def service(self, handler_call_details):
        return grpc.unary_unary_rpc_method_handler(self._handler)


@snapshot(ignores=["meta.grpc.port"])
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
