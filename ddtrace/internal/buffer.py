from collections import deque
import threading

from ddtrace.vendor import attr


class BufferFull(Exception):
    pass


class BufferItemTooLarge(Exception):
    pass


@attr.s
class TraceBuffer(object):
    """A thread-safe buffer for collecting encoded trace payloads to be sent to
    a Datadog Agent.

    :param max_size: The maximum size (in bytes) of the buffer.
    :param max_item_size: The maximum size of any item in the buffer. It is necessary
        to have an item limit as traces cannot be divided across trace payloads.
    """

    max_size = attr.ib(type=int)
    max_item_size = attr.ib(type=int)
    _size = attr.ib(init=False, type=int, default=0, repr=True)
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)
    _buffer = attr.ib(init=False, factory=deque, repr=False)

    def __len__(self):
        return len(self._buffer)

    @property
    def size(self):
        """Return the size in bytes of the trace buffer."""
        with self._lock:
            return self._size

    def _clear(self):
        self._buffer.clear()
        self._size = 0

    def put(self, item):
        """Put an item in the buffer.

        The item should be an encoded trace (list of spans).
        """
        item_len = len(item)
        if item_len > self.max_item_size or item_len > self.max_size:
            raise BufferItemTooLarge()

        with self._lock:
            if self._size + item_len <= self.max_size:
                self._buffer.append(item)
                self._size += item_len
            else:
                raise BufferFull()

    def get(self):
        """Return the entire buffer.

        The buffer is cleared in the process.
        """
        with self._lock:
            try:
                return list(self._buffer)
            finally:
                self._clear()
