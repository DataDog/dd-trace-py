# Thirdparty
import grpc
from grpc.framework.foundation import logging_pool

# Internal
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.grpc import patch, unpatch
from ddtrace import Pin

from ...base import BaseTracerTestCase

from .hello_pb2 import HelloRequest, HelloReply
from .hello_pb2_grpc import add_HelloServicer_to_server, HelloStub

GRPC_PORT = 50531


class GrpcTestCase(BaseTracerTestCase):
    def setUp(self):
        super(GrpcTestCase, self).setUp()

        patch()
        Pin.override(grpc, tracer=self.tracer)
        self._server = grpc.server(logging_pool.pool(2))
        self._server.add_insecure_port('[::]:%d' % (GRPC_PORT))
        add_HelloServicer_to_server(SendBackDatadogHeaders(), self._server)
        self._server.start()

    def tearDown(self):
        unpatch()
        self._server.stop(5)

        super(GrpcTestCase, self).tearDown()

    def _check_span(self, span, service='grpc'):
        self.assertEqual(span.name, 'grpc.client')
        self.assertEqual(span.resource, '/Hello/SayHello')
        self.assertEqual(span.service, service)
        self.assertEqual(span.error, 0)
        self.assertEqual(span.span_type, 'grpc')
        self.assertEqual(span.meta['grpc.host'], 'localhost')
        self.assertEqual(span.meta['grpc.port'], '50531')

    def test_insecure_channel(self):
        # Create a channel and send one request to the server
        target = 'localhost:%d' % (GRPC_PORT)
        with grpc.insecure_channel(target=target) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(
            response.message,
            (
                # DEV: Priority sampling is enabled by default
                'x-datadog-trace-id=%d;x-datadog-parent-id=%d;x-datadog-sampling-priority=1' %
                (span.trace_id, span.span_id)
            ),
        )
        self._check_span(span)

    def test_secure_channel(self):
        # Create a channel and send one request to the server
        target = 'localhost:%d' % (GRPC_PORT)
        with grpc.secure_channel(target=target, credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)

        span = spans[0]
        self.assertEqual(
            response.message,
            (
                # DEV: Priority sampling is enabled by default
                'x-datadog-trace-id=%d;x-datadog-parent-id=%d;x-datadog-sampling-priority=1' %
                (span.trace_id, span.span_id)
            ),
        )
        self._check_span(span)

    def test_priority_sampling(self):
        # DEV: Priority sampling is enabled by default
        # Setting priority sampling reset the writer, we need to re-override it

        # Create a channel and send one request to the server
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]

        self.assertEqual(
            response.message,
            (
                'x-datadog-trace-id=%d;x-datadog-parent-id=%d;x-datadog-sampling-priority=1' %
                (span.trace_id, span.span_id)
            ),
        )
        self._check_span(span)

    def test_span_in_error(self):
        # Create a channel and send one request to the server
        with grpc.secure_channel('localhost:%d' % (GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            with self.assertRaises(Exception):
                stub.SayError(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)

        span = spans[0]
        self.assertEqual(span.error, 1)
        self.assertIsNotNone(span.meta['error.stack'])

    def test_pin_not_activated(self):
        self.tracer.configure(enabled=False)
        Pin.override(grpc, tracer=self.tracer)
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 0)

    def test_pin_tags_are_put_in_span(self):
        Pin.override(grpc, tags={'tag1': 'value1'})
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.meta['tag1'], 'value1')

    def test_pin_can_be_defined_per_channel(self):
        Pin.override(grpc, service='grpc1')
        channel1 = grpc.insecure_channel('localhost:%d' % (GRPC_PORT))

        Pin.override(grpc, service='grpc2')
        channel2 = grpc.insecure_channel('localhost:%d' % (GRPC_PORT))

        stub1 = HelloStub(channel1)
        stub2 = HelloStub(channel2)
        stub1.SayHello(HelloRequest(name='test'))
        stub2.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()

        self.assertEqual(len(spans), 2)
        span1 = spans[0]
        span2 = spans[1]
        self._check_span(span1, 'grpc1')
        self._check_span(span2, 'grpc2')

        channel1.close()
        channel2.close()

    def test_analytics_default(self):
        with grpc.secure_channel('localhost:%d' % (GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config(
            'grpc',
            dict(analytics_enabled=True, analytics_sample_rate=0.5)
        ):
            with grpc.secure_channel(
                    'localhost:%d' % (GRPC_PORT),
                    credentials=grpc.ChannelCredentials(None)
            ) as channel:
                stub = HelloStub(channel)
                stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config(
            'grpc',
            dict(analytics_enabled=True)
        ):
            with grpc.secure_channel(
                    'localhost:%d' % (GRPC_PORT),
                    credentials=grpc.ChannelCredentials(None)
            ) as channel:
                stub = HelloStub(channel)
                stub.SayHello(HelloRequest(name='test'))

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)


class SendBackDatadogHeaders(object):
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
        context.set_code(grpc.StatusCode.ABORTED)
        context.cancel()
        return HelloReply(message='cancelled')
