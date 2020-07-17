import pytest
import ddtrace
import random

from ddtrace.vendor.six.moves.queue import Queue, Full, Empty
from ddtrace.internal._queue import TraceQueue

# Old queue
class Q(Queue):
    """
    Q is a threadsafe queue that let's you pop everything at once and
    will randomly overwrite elements when it's over the max size.
    This queue also exposes some statistics about its length, the number of items dropped, etc.
    """

    def __init__(self, maxsize=0):
        # Cannot use super() here because Queue in Python2 is old style class
        Queue.__init__(self, maxsize)
        # Number of item dropped (queue full)
        self.dropped = 0
        # Number of items accepted
        self.accepted = 0
        # Cumulative length of accepted items
        self.accepted_lengths = 0

    def put(self, item):
        try:
            # Cannot use super() here because Queue in Python2 is old style class
            Queue.put(self, item, block=False)
        except Full:
            # If the queue is full, replace a random item. We need to make sure
            # the queue is not emptied was emptied in the meantime, so we lock
            # check qsize value.
            with self.mutex:
                qsize = self._qsize()
                if qsize >= self.maxsize:
                    idx = random.randrange(0, qsize)
                    self.queue[idx] = item
                    self.dropped += 1
                    self._update_stats(item)
                    return
            # The queue has been emptied, simply retry putting item
            return self.put(item)
        else:
            with self.mutex:
                self._update_stats(item)

    def _update_stats(self, item):
        # self.mutex needs to be locked to make sure we don't lose data when resetting
        self.accepted += 1
        if hasattr(item, "__len__"):
            item_length = len(item)
        else:
            item_length = 1
        self.accepted_lengths += item_length

    def reset_stats(self):
        """Reset the stats to 0.
        :return: The current value of dropped, accepted and accepted_lengths.
        """
        with self.mutex:
            dropped, accepted, accepted_lengths = (self.dropped, self.accepted, self.accepted_lengths)
            self.dropped, self.accepted, self.accepted_lengths = 0, 0, 0
        return dropped, accepted, accepted_lengths

    def _get(self):
        things = self.queue
        self._init(self.maxsize)
        return things


@pytest.mark.benchmark(group="create_spans", min_time=0.005)
def test_tracer_trace_queue_spans(benchmark):
    ddtrace.tracer.writer._trace_queue = TraceQueue(maxsize=100000)

    @benchmark
    def f():
        for i in range(10):
            ddtrace.tracer.trace("operation").finish()


@pytest.mark.benchmark(group="create_spans", min_time=0.005)
def test_tracer_q_spans(benchmark):
    ddtrace.tracer.writer._trace_queue = Q(maxsize=1000000)

    @benchmark
    def f():
        for i in range(10):
            ddtrace.tracer.trace("operation").finish()


@pytest.mark.benchmark(group="putget", min_time=0.005)
def test_tracer_q(benchmark):
    q = Q(maxsize=1000)

    @benchmark
    def f():
        for i in range(500):
            q.put([])
            q.get()


@pytest.mark.benchmark(group="putget", min_time=0.005)
def test_tracer_trace_queue(benchmark):
    q = TraceQueue(maxsize=1000)

    @benchmark
    def f():
        for i in range(500):
            q.put([])
            q.get()
