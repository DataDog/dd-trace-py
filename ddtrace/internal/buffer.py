from collections import deque
import threading

from ddtrace.vendor import attr


class BufferFull(Exception):
    pass


class BufferItemTooLarge(Exception):
    pass


@attr.s
class TraceBuffer(object):

    max_size = attr.ib(type=int)
    max_item_size = attr.ib(type=int)
    _size = attr.ib(init=False, type=int, default=0, repr=True)
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)
    _buffer = attr.ib(init=False, factory=deque, repr=False)

    def __len__(self):
        return len(self._buffer)

    @property
    def size(self):
        with self._lock:
            return self._size

    def _clear(self):
        self._buffer.clear()
        self._size = 0

    def put(self, item):
        if len(item) > self.max_item_size or len(item) > self.max_size:
            raise BufferItemTooLarge()

        with self._lock:
            if self._size + len(item) <= self.max_size:
                self._buffer.append(item)
                self._size += len(item)
            else:
                raise BufferFull()

    def popleft(self):
        i = self._buffer.popleft()
        self._size -= len(i)
        return i

    def get(self, size):
        with self._lock:
            # Find and pop up to `size` amount of traces.
            if self._size <= size:
                try:
                    return list(self._buffer)
                finally:
                    self._clear()
            else:
                b = []
                b_size = 0
                while len(self._buffer):
                    if len(self._buffer[0]) + b_size <= size:
                        i = self.popleft()
                        b_size += len(i)
                        b.append(i)
                    else:
                        break
                return b


TraceBuffer.BufferFull = BufferFull
TraceBuffer.BufferItemTooLarge = BufferItemTooLarge
