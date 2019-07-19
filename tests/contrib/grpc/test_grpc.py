import grpc
from grpc.framework.foundation import logging_pool
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.grpc import patch, unpatch
from ddtrace import Pin

from ...base import BaseTracerTestCase

from .hello_pb2 import HelloRequest, HelloReply
from .hello_pb2_grpc import add_HelloServicer_to_server, HelloStub, HelloServicer

_GRPC_PORT = 50531


class GrpcTestCase(BaseTracerTestCase):
    def setUp(self):
        super(GrpcTestCase, self).setUp()

        patch()

        Pin.override(grpc, tracer=self.tracer)
        self._start_server()

    def tearDown(self):
        self._stop_server()

        # Remove any remaining spans
        self.tracer.writer.pop()

        # Unpatch grpc
        unpatch()

        super(GrpcTestCase, self).tearDown()

    def _start_server(self):
        self._server = grpc.server(logging_pool.pool(2))
        self._server.add_insecure_port('[::]:%d' % (_GRPC_PORT))
        add_HelloServicer_to_server(SendBackDatadogHeaders(), self._server)
        self._server.start()

    def _stop_server(self):
        self._server.stop(0)

    def _check_client_span(self, span, service='grpc'):
        assert span.name == 'grpc'
        assert span.resource == '/helloworld.Hello/SayHello'
        assert span.service == service
        assert span.error == 0
        assert span.span_type == 'grpc'
        assert span.get_tag('grpc.method.path') == '/helloworld.Hello/SayHello'
        assert span.get_tag('grpc.method.package') == 'helloworld'
        assert span.get_tag('grpc.method.service') == 'Hello'
        assert span.get_tag('grpc.method.name') == 'SayHello'
        assert span.get_tag('grpc.method.kind') == 'unary'
        assert span.get_tag('grpc.host') == 'localhost'
        assert span.get_tag('grpc.port') == '50531'

    def _check_server_span(self, span, service='grpc'):
        assert span.name == 'grpc'
        assert span.resource == '/helloworld.Hello/SayHello'
        assert span.service == service
        assert span.error == 0
        assert span.span_type == 'grpc'
        assert span.get_tag('grpc.method.path') == '/helloworld.Hello/SayHello'
        assert span.get_tag('grpc.method.package') == 'helloworld'
        assert span.get_tag('grpc.method.service') == 'Hello'
        assert span.get_tag('grpc.method.name') == 'SayHello'

    def test_insecure_channel_using_args_parameter(self):
        def insecure_channel_using_args(target):
            return grpc.insecure_channel(target)
        self._test_insecure_channel(insecure_channel_using_args)

    def test_insecure_channel_using_kwargs_parameter(self):
        def insecure_channel_using_kwargs(target):
            return grpc.insecure_channel(target=target)
        self._test_insecure_channel(insecure_channel_using_kwargs)

    def _test_insecure_channel(self, insecure_channel_function):
        # Create a channel and send one request to the server
        target = 'localhost:%d' % (_GRPC_PORT)
        with insecure_channel_function(target) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        server_span, client_span = spans

        assert 'x-datadog-trace-id={}'.format(client_span.trace_id) in response.message
        assert 'x-datadog-parent-id={}'.format(client_span.span_id) in response.message
        assert 'x-datadog-sampling-priority=1' in response.message

        self._check_client_span(client_span)
        self._check_server_span(server_span)

    def test_secure_channel_using_args_parameter(self):
        def secure_channel_using_args(target, **kwargs):
            return grpc.secure_channel(target, **kwargs)
        self._test_secure_channel(secure_channel_using_args)

    def test_secure_channel_using_kwargs_parameter(self):
        def secure_channel_using_kwargs(target, **kwargs):
            return grpc.secure_channel(target=target, **kwargs)
        self._test_secure_channel(secure_channel_using_kwargs)

    def _test_secure_channel(self, secure_channel_function):
        # Create a channel and send one request to the server
        target = 'localhost:%d' % (_GRPC_PORT)
        with secure_channel_function(target, credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        server_span, client_span = spans

        assert 'x-datadog-trace-id={}'.format(client_span.trace_id) in response.message
        assert 'x-datadog-parent-id={}'.format(client_span.span_id) in response.message
        assert 'x-datadog-sampling-priority=1' in response.message

        self._check_client_span(client_span)
        self._check_server_span(server_span)

    def test_priority_sampling(self):
        # DEV: Priority sampling is enabled by default
        # Setting priority sampling reset the writer, we need to re-override it

        # Create a channel and send one request to the server
        with grpc.insecure_channel('localhost:%d' % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        server_span, client_span = spans

        assert 'x-datadog-trace-id={}'.format(client_span.trace_id) in response.message
        assert 'x-datadog-parent-id={}'.format(client_span.span_id) in response.message
        assert 'x-datadog-sampling-priority=1' in response.message

        self._check_client_span(client_span)
        self._check_server_span(server_span)

    def test_span_aborted(self):
        # Create a channel and send one request to the server
        with grpc.secure_channel('localhost:%d' % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(Exception):
                stub.SayError(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        server_span, client_span = spans

        assert client_span.resource == '/helloworld.Hello/SayError'
        assert client_span.get_tag('rpc_error.status') == 'StatusCode.ABORTED'
        assert client_span.get_tag('rpc_error.details') == 'aborted'

        assert server_span.resource == '/helloworld.Hello/SayError'
        assert server_span.get_tag('rpc_error.status') == 'StatusCode.ABORTED'
        assert server_span.get_tag('rpc_error.details') == 'aborted'

    def test_pin_not_activated(self):
        self.tracer.configure(enabled=False)
        with grpc.insecure_channel('localhost:%d' % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 0

    def test_pin_tags_are_put_in_span(self):
        # DEV: stop and restart server to catch overriden pin
        self._stop_server()
        Pin.override(grpc, tags={'tag1': 'value1'})
        self._start_server()
        Pin.override(grpc, tags={'tag1': 'value2'})
        with grpc.insecure_channel('localhost:%d' % (_GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        assert spans[0].get_tag('tag1') == 'value1'
        assert spans[1].get_tag('tag1') == 'value2'

    def test_pin_can_be_defined_per_channel(self):
        Pin.override(grpc, service='grpc1')
        channel1 = grpc.insecure_channel('localhost:%d' % (_GRPC_PORT))

        Pin.override(grpc, service='grpc2')
        channel2 = grpc.insecure_channel('localhost:%d' % (_GRPC_PORT))

        stub1 = HelloStub(channel1)
        stub2 = HelloStub(channel2)
        stub1.SayHello(HelloRequest(name='test'))
        stub2.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()

        assert len(spans) == 4
        # DEV: Server service default, client services override
        self._check_server_span(spans[0], 'grpc')
        self._check_client_span(spans[1], 'grpc1')
        self._check_server_span(spans[2], 'grpc')
        self._check_client_span(spans[3], 'grpc2')

        channel1.close()
        channel2.close()

    def test_analytics_default(self):
        with grpc.secure_channel('localhost:%d' % (_GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None
        assert spans[1].get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_analytics_with_rate(self):
        with self.override_config(
            'grpc',
            dict(analytics_enabled=True, analytics_sample_rate=0.5)
        ):
            with grpc.secure_channel(
                    'localhost:%d' % (_GRPC_PORT),
                    credentials=grpc.ChannelCredentials(None)
            ) as channel:
                stub = HelloStub(channel)
                stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5
        assert spans[1].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    def test_analytics_without_rate(self):
        with self.override_config(
            'grpc',
            dict(analytics_enabled=True)
        ):
            with grpc.secure_channel(
                    'localhost:%d' % (_GRPC_PORT),
                    credentials=grpc.ChannelCredentials(None)
            ) as channel:
                stub = HelloStub(channel)
                stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        assert len(spans) == 2
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0
        assert spans[1].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0


class SendBackDatadogHeaders(HelloServicer):
    def SayHello(self, request, context):
        """Returns all the headers begining by x-datadog with the following format:
        header1=value1;header2=value2;...
        It is used to test propagation
        """
        metadata = context.invocation_metadata()
        context.set_code(grpc.StatusCode.OK)
        return HelloReply(
            message=';'.join(w.key + '=' + w.value for w in metadata if w.key.startswith('x-datadog')),
        )

    def SayError(self, request, context):
        context.abort(grpc.StatusCode.ABORTED, 'aborted')
