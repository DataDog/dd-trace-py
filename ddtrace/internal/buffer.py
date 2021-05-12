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


@attr.s
class TraceBuffer(object):
    """A thread-safe buffer for collecting traces to be sent to a Datadog Agent.

    :param max_size: The maximum size (in bytes) of the buffer.
    :param max_item_size: The maximum size of any item in the buffer. It is necessary
        to have an item limit as traces cannot be divided across trace payloads.
    """

    max_size = attr.ib(type=int)
    max_item_size = attr.ib(type=int)
    meter = attr.ib(type=callable, repr=False)
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
        item_len = self.meter(trace)
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
