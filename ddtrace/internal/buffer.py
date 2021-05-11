from collections import deque
import threading
from typing import Deque
from typing import List

import attr

from ddtrace.span import Span


class BufferFull(Exception):
    pass


class BufferItemTooLarge(Exception):
    pass


def trace_size(trace):
    """Heuristic to determine the approximate size of a trace in bytes.

    The estimate is pessimistic and tends to return a value higher than the
    real payload size.
    """
    STRING_LENGTH = 1024
    KEY_LENGTH = 256
    METRIC_VALUE_SIZE = 8

    size = 0
    for span in trace:
        n_meta = len(span.meta)
        n_metrics = len(span.metrics)
        size += KEY_LENGTH * (4 + n_meta + n_metrics) + METRIC_VALUE_SIZE * (6 + n_metrics) + STRING_LENGTH * n_meta
    return size


# DEV: This implementation is heavy (~1.22% overhead) but more accurate
# def trace_size(trace):
#     """Heuristic to determine the approximate size of a trace in bytes."""
#     size = 0
#     for span in trace:
#         size += 92
#         if span.name:
#             size += len(span.name)
#         if span.resource:
#             size += len(span.resource)
#         if span.service:
#             size += len(span.service)
#         if span._span_type:
#             size += len(span._span_type)
#         if span.meta:
#             size += sum((len(k) + len(v) for k, v in span.meta.items()))
#         if span.metrics:
#             size += sum((len(k) + 9 for k in span.metrics))
#     return size


@attr.s
class TraceBuffer(object):
    """A thread-safe buffer for collecting traces to be sent to a Datadog Agent.

    :param max_size: The maximum size (in bytes) of the buffer.
    :param max_item_size: The maximum size of any item in the buffer. It is necessary
        to have an item limit as traces cannot be divided across trace payloads.
    """

    max_size = attr.ib(type=int)
    max_item_size = attr.ib(type=int)
    _size = attr.ib(init=False, type=int, default=0, repr=True)
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)
    _traces = attr.ib(init=False, factory=deque, repr=False, type=Deque[list])

    def __len__(self):
        return len(self._traces)

    @property
    def size(self):
        # type: () -> int
        """Return the size in bytes of the trace buffer."""
        with self._lock:
            return self._size

    def _clear(self):
        # type: () -> None
        self._traces.clear()
        self._size = 0

    def put(self, trace):
        # type: (List[Span]) -> None
        """Put a trace (i.e. a list of spans) in the buffer."""
        item_len = trace_size(trace)
        if item_len > self.max_item_size or item_len > self.max_size:
            raise BufferItemTooLarge(item_len)

        with self._lock:
            if self._size + item_len <= self.max_size:
                self._traces.append(trace)
                self._size += item_len
            else:
                raise BufferFull(item_len)

    def get(self):
        # type: () -> List[List[Span]]
        """Return the entire buffer.

        The buffer is cleared in the process.
        """
        with self._lock:
            try:
                return list(self._traces)
            finally:
                self._clear()
