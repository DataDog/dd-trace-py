from collections import deque
import threading

from ddtrace.vendor import attr
from ..internal.logger import get_logger
from . import _rand


log = get_logger(__name__)


@attr.s
class TraceBuffer(object):

    maxsize = attr.ib(type=int, default=0)
    _size = attr.ib(init=False, type=int, default=0, repr=True)
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)
    _buffer = attr.ib(init=False, factory=deque, repr=False)
    _accepted = attr.ib(init=False, type=int, default=0)
    _accepted_lengths = attr.ib(init=False, type=int, default=0)
    _dropped = attr.ib(init=False, type=int, default=0)

    def __len__(self):
        return len(self._buffer)

    @property
    def size(self):
        with self._lock:
            return self._size

    def clear(self):
        self._buffer.clear()
        self._size = 0

    def put(self, item):
        with self._lock:
            if self.maxsize <= 0 or self._size + len(item) < self.maxsize:
                self._buffer.append(item)
            else:
                self._dropped += 1
                self._dropped_size += len(item)
                log.warning("trace buffer %r is full, dropping a random trace", self)

            self._accepted += 1
            self._accepted_lengths += len(item)

    def popleft(self):
        i = self._buffer.popleft()
        self._size -= len(i)
        return i

    def get(self, size=None):
        size = size or self.maxsize
        with self._lock:
            # Find and pop up to `size` amount of traces.
            if self._size <= size:
                try:
                    return list(self._buffer)
                finally:
                    self.clear()
            else:
                b = []
                b_size = 0
                while len(self._buffer):
                    if len(self._buffer[0]) + b_size < size:
                        i = self.popleft()
                        b_size += len(i)
                        b.append(i)
                    else:
                        break
                return b

    def pop_stats(self):
        with self._lock:
            try:
                return self._dropped, self._accepted, self._accepted_lengths
            finally:
                self._accepted = 0
                self._accepted_lengths = 0
                self._dropped = 0
