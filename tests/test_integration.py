import mock
import time

from unittest import TestCase
from nose.tools import eq_

from ddtrace.tracer import Tracer


class TestIntegration(TestCase):
    """
    Ensures that a traces are properly sent to a local agent. These are
    integration tests so real calls are fired and you have to execute
    a real trace-agent in order to let them pass.
    """
    def setUp(self):
        """
        Create a tracer with running workers, while spying the ``send()`` method
        """
        self.tracer = Tracer()
        self.transport = self.tracer.writer._reporter._transport
        self.transport.send = mock.Mock(self.transport.send, wraps=self.transport.send)
        self.workers = self.tracer.writer._reporter._workers

    def tearDown(self):
        """
        Stop running workers
        """
        self.tracer.writer._reporter.stop()

    def test_a_trace_is_sent(self):
        # create a trace block and send it using the transport system
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        self.workers[0].join()
        eq_(self.transport.send.call_count, 1)

    def test_single_send_for_multiple_traces(self):
        # make a single send() if multiple traces are created before the flush interval
        tracer = self.tracer
        tracer.trace('client.testing').finish()
        tracer.trace('client.testing').finish()
        self.workers[0].join()
        eq_(self.transport.send.call_count, 1)
