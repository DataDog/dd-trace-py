# Standard library
import time
import unittest

# Thirdparty
import grpc
from grpc.framework.foundation import logging_pool
from nose.tools import eq_
import wrapt

# Internal
from ddtrace.contrib.grpc import patch, unpatch
from ddtrace.contrib.grpc import client_interceptor
from ddtrace import Pin


from ...test_tracer import get_dummy_tracer, DummyWriter

from .hello_pb2 import HelloRequest, HelloReply
from .hello_pb2_grpc import add_HelloServicer_to_server, HelloServicer, HelloStub

GRPC_PORT = 50531

class GrpcBaseMixin(object):
    def setUp(self):
        patch()
        self._tracer = get_dummy_tracer()
        Pin.override(grpc, tracer=self._tracer)
        self._server = grpc.server(logging_pool.pool(2))
        self._server.add_insecure_port('[::]:%d' % (GRPC_PORT))
        add_HelloServicer_to_server(SendBackDatadogHeaders(), self._server)
        self._server.start()

    def tearDown(self):
        unpatch()
        self._server.stop(5)


class GrpcTestCase(GrpcBaseMixin, unittest.TestCase):
    def test_insecure_channel(self):
        # Create a channel and send one request to the server
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        writer = self._tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(response.message, 'x-datadog-trace-id=%d;x-datadog-parent-id=%d' % (span.trace_id, span.span_id))
        _check_span(span)

    def test_secure_channel(self):
        # Create a channel and send one request to the server
        with grpc.secure_channel('localhost:%d' % (GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        writer = self._tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        
        span = spans[0]
        eq_(response.message, 'x-datadog-trace-id=%d;x-datadog-parent-id=%d' % (span.trace_id, span.span_id))
        _check_span(span)

    def test_priority_sampling(self):
        self._tracer.configure(priority_sampling=True)
        # Setting priority sampling reset the writer, we need to re-override it
        self._tracer.writer = DummyWriter()

        # Create a channel and send one request to the server
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            response = stub.SayHello(HelloRequest(name='test'))

        writer = self._tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]

        eq_(
            response.message,
            'x-datadog-trace-id=%d;x-datadog-parent-id=%d;x-datadog-sampling-priority=1' % (span.trace_id, span.span_id),
        )
        _check_span(span)

    def test_span_in_error(self):
        # Create a channel and send one request to the server
        with grpc.secure_channel('localhost:%d' % (GRPC_PORT), credentials=grpc.ChannelCredentials(None)) as channel:
            stub = HelloStub(channel)
            try:
                stub.SayError(HelloRequest(name='test'))
            except:
                pass # excepted to throw

        writer = self._tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        
        span = spans[0]
        eq_(span.error, 1)
        self.assertIsNotNone(span.meta['error.stack'])

    def test_pin_not_activated(self):
        self._tracer.configure(enabled=False)
        Pin.override(grpc, tracer=self._tracer)
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        writer = self._tracer.writer
        spans = writer.pop()
        eq_(len(spans), 0)

    def test_pin_tags_are_put_in_span(self):
        Pin.override(grpc, tags={'tag1': 'value1'})
        with grpc.insecure_channel('localhost:%d' % (GRPC_PORT)) as channel:
            stub = HelloStub(channel)
            stub.SayHello(HelloRequest(name='test'))

        writer = self._tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.meta['tag1'], 'value1')

    def test_pin_can_be_defined_per_channel(self):
        Pin.override(grpc, service='grpc1')
        channel1 = grpc.insecure_channel('localhost:%d' % (GRPC_PORT))

        Pin.override(grpc, service='grpc2')
        channel2 = grpc.insecure_channel('localhost:%d' % (GRPC_PORT))

        stub1 = HelloStub(channel1)
        stub2 = HelloStub(channel2)
        stub1.SayHello(HelloRequest(name='test'))
        stub2.SayHello(HelloRequest(name='test'))

        writer = self._tracer.writer
        spans = writer.pop()
        
        eq_(len(spans), 2)
        span1 = spans[0]
        span2 = spans[1]
        _check_span(span1, 'grpc1')
        _check_span(span2, 'grpc2')

        channel1.close()
        channel2.close()


def _check_span(span, service='grpc'):
    eq_(span.name, 'grpc.client')
    eq_(span.resource, '/Hello/SayHello')
    eq_(span.service, service)
    eq_(span.error, 0)
    eq_(span.span_type, 'grpc')
    eq_(span.meta['grpc.host'], 'localhost')
    eq_(span.meta['grpc.port'], '50531')


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
