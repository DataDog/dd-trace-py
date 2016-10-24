import os
import mock
import time

from unittest import TestCase, skipUnless
from nose.tools import eq_, ok_

from ddtrace.tracer import Tracer


class TestWorkers(TestCase):
    """
    Ensures that a workers interacts correctly with the main thread. These are part
    of integration tests so real calls are fired. A test must be considered FAILED
    if an assert fails or if the ``join()`` hangs for some reasons.
    """
    def setUp(self):
        """
        Create a tracer with running workers, while spying the ``send()`` method
        """
        # create a new tracer
        self.tracer = Tracer()
        # force a fast flush for both workers
        self.tracer.configure(flush_interval=0.1, service_interval=0.1)
        # spy the send() method
        self.transport = self.tracer.writer._transport
        self.transport.send = mock.Mock(self.transport.send, wraps=self.transport.send)
        self.workers = self.tracer.writer._workers

    def tearDown(self):
        """
        Stop running workers
        """
        self.tracer.writer.stop()

    def test_worker_single_trace(self):
        # create a trace block and send it using the transport system
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        # the trace is in the transporter buffer
        eq_(self.workers[0]._buffer.size(), 1)
        # one send is expected
        self.workers[0].join()
        eq_(self.transport.send.call_count, 1)

    def test_worker_multiple_traces(self):
        # make a single send() if multiple traces are created before the flush interval
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        tracer.trace('client.testing').finish()
        # the trace is in the transporter buffer
        eq_(self.workers[0]._buffer.size(), 2)
        # one send is expected
        self.workers[0].join()
        eq_(self.transport.send.call_count, 1)

    def test_worker_single_trace_multiple_spans(self):
        # make a single send() if a single trace with multiple spans is created before the flush
        tracer = self.tracer
        parent = tracer.trace('client.testing')
        child = tracer.trace('client.testing').finish()
        parent.finish()
        # the trace is in the transporter buffer
        eq_(self.workers[0]._buffer.size(), 1)
        trace = self.workers[0]._buffer._queue[0]
        # we have two spans in this trace
        eq_(len(trace), 2)
        # one send is expected
        self.workers[0].join()
        eq_(self.transport.send.call_count, 1)

    def test_worker_single_service(self):
        # service must be sent correctly
        tracer = self.tracer
        tracer.set_service_info('client.service', 'django', 'web')
        # the service list is in the transporter buffer
        eq_(self.workers[1]._buffer.size(), 1)
        # one send is expected
        self.workers[1].join()
        eq_(self.transport.send.call_count, 1)

    def test_worker_service_called_multiple_times(self):
        # service must be sent correctly
        tracer = self.tracer
        tracer.set_service_info('backend', 'django', 'web')
        tracer.set_service_info('database', 'postgres', 'db')
        # the service list is in the transporter buffer
        eq_(self.workers[1]._buffer.size(), 1)
        services = self.workers[1]._buffer._queue[0]
        eq_(services['backend']['app'], 'django')
        eq_(services['database']['app'], 'postgres')
        # one send is expected
        self.workers[1].join()
        eq_(self.transport.send.call_count, 1)


@skipUnless(
    os.environ.get('DATADOG_INTEGRATION_TESTS', False),
    'You should have a running trace agent and set the DATADOG_INTEGRATION_TESTS env variable'
)
class TestDefaultTransport(TestCase):
    """
    Ensures that a traces are properly sent to a local agent. These are part
    of integration tests so real calls are fired and you have to execute
    a real trace-agent in order to let them pass.
    """
    def setUp(self):
        """
        Create a tracer without workers, while spying the ``send()`` method
        """
        # create a new tracer and stop all running workers; we will
        # test the transport using synchronous calls
        self.tracer = Tracer()
        self.tracer.writer.stop()
        # spy the send() method
        self.transport = self.tracer.writer._transport
        self.transport.send = mock.Mock(self.transport.send, wraps=self.transport.send)
        self.workers = self.tracer.writer._workers

    def test_send_single_trace(self):
        # register some traces and send them to the trace agent
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        spans = self.workers[0]._buffer.pop()
        response = tracer.writer.send_traces(spans, self.transport)
        ok_(response)
        eq_(response.status, 200)

    def test_send_multiple_traces(self):
        # register some traces and send them to the trace agent
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        tracer.trace('client.testing').finish()
        spans = self.workers[0]._buffer.pop()
        response = tracer.writer.send_traces(spans, self.transport)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_trace_multiple_spans(self):
        # register some traces and send them to the trace agent
        tracer = self.tracer
        parent = tracer.trace('client.testing')
        child = tracer.trace('client.testing').finish()
        parent.finish()
        spans = self.workers[0]._buffer.pop()
        response = tracer.writer.send_traces(spans, self.transport)
        ok_(response)
        eq_(response.status, 200)

    def test_send_single_service(self):
        # register some services and send them to the trace agent
        tracer = self.tracer
        tracer.set_service_info('client.service', 'django', 'web')
        services = self.workers[1]._buffer.pop()
        response = tracer.writer.send_services(services, self.transport)
        ok_(response)
        eq_(response.status, 200)

    def test_send_service_called_multiple_times(self):
        # register some services and send them to the trace agent
        tracer = self.tracer
        tracer.set_service_info('backend', 'django', 'web')
        tracer.set_service_info('database', 'postgres', 'db')
        services = self.workers[1]._buffer.pop()
        response = tracer.writer.send_services(services, self.transport)
        ok_(response)
        eq_(response.status, 200)
