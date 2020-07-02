import threading

from ddtrace.vendor import attr
from ..internal.logger import get_logger
from . import _rand


log = get_logger(__name__)


@attr.s
class TraceQueue(object):

    maxsize = attr.ib(type=int, default=0)
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)
    _queue = attr.ib(init=False, factory=list, repr=False)
    _accepted = attr.ib(init=False, type=int, default=0)
    _accepted_lengths = attr.ib(init=False, type=int, default=0)
    _dropped = attr.ib(init=False, type=int, default=0)

    def __len__(self):
        return len(self._queue)

    def put(self, item):
        with self._lock:
            if self.maxsize <= 0 or len(self._queue) < self.maxsize:
                self._queue.append(item)
            else:
                idx = _rand.rand64bits() % len(self._queue)
                self._queue[idx] = item

                self._dropped += 1
                log.warning("Trace queue %r is full, dropping a random trace", self)

            self._accepted += 1
            self._accepted_lengths += len(item) if hasattr(item, "__len__") else 1

    def get(self):
        with self._lock:
            try:
                return self._queue
            finally:
                self._queue = []

    def pop_stats(self):
        with self._lock:
            try:
                return self._dropped, self._accepted, self._accepted_lengths
            finally:
                self._accepted = 0
                self._accepted_lengths = 0
                self._dropped = 0
