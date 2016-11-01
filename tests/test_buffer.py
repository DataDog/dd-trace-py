import random
import threading

from nose.tools import eq_, ok_
from unittest import TestCase

from ddtrace.span import Span
from ddtrace.buffer import ThreadLocalSpanBuffer, TraceBuffer


class TestInternalBuffers(TestCase):
    """
    Tests related to the client internal buffers
    """
    def test_thread_local_buffer(self):
        # the internal buffer must be thread-safe
        tb = ThreadLocalSpanBuffer()

        def _set_get():
            eq_(tb.get(), None)
            span = Span(tracer=None, name='client.testing')
            tb.set(span)
            eq_(span, tb.get())

        threads = [threading.Thread(target=_set_get) for _ in range(20)]

        for t in threads:
            t.daemon = True
            t.start()

        for t in threads:
            t.join()

    def test_trace_buffer_limit(self):
        # the trace buffer must have a limit, if the limit is reached a
        # trace must be discarded
        trace_buff = TraceBuffer(maxsize=1)
        span_1 = Span(tracer=None, name='client.testing')
        span_2 = Span(tracer=None, name='client.testing')
        trace_buff.push(span_1)
        trace_buff.push(span_2)
        eq_(len(trace_buff._queue), 1)
        eq_(trace_buff._queue[0], span_2)

    def test_trace_buffer_empty(self):
        # empty should work as expected
        trace_buff = TraceBuffer(maxsize=1)
        eq_(trace_buff.empty(), True)
        span = Span(tracer=None, name='client.testing')
        trace_buff.push(span)
        eq_(trace_buff.empty(), False)

    def test_trace_buffer_pop(self):
        # the trace buffer must return all internal traces
        trace_buff = TraceBuffer()
        span_1 = Span(tracer=None, name='client.testing')
        span_2 = Span(tracer=None, name='client.testing')
        trace_buff.push(span_1)
        trace_buff.push(span_2)
        eq_(len(trace_buff._queue), 2)

        # get the traces and be sure that the queue is empty
        traces = trace_buff.pop()
        eq_(len(trace_buff._queue), 0)
        eq_(len(traces), 2)
        ok_(span_1 in traces)
        ok_(span_2 in traces)

    def test_trace_buffer_without_cap(self):
        # the trace buffer must have unlimited size if users choose that
        trace_buff = TraceBuffer(maxsize=0)
        span_1 = Span(tracer=None, name='client.testing')
        span_2 = Span(tracer=None, name='client.testing')
        trace_buff.push(span_1)
        trace_buff.push(span_2)
        eq_(len(trace_buff._queue), 2)
        ok_(span_1 in trace_buff._queue)
        ok_(span_2 in trace_buff._queue)
