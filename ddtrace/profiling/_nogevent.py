# -*- encoding: utf-8 -*-
"""This files exposes non-gevent Python original functions."""
import threading

from ddtrace.vendor import six
from ddtrace.vendor import attr


try:
    import gevent.monkey
except ImportError:

    def get_original(module, func):
        return getattr(__import__(module), func)

    def is_module_patched(module):
        return False


else:
    get_original = gevent.monkey.get_original
    is_module_patched = gevent.monkey.is_module_patched


sleep = get_original("time", "sleep")

try:
    # Python ≥ 3.8
    threading_get_native_id = get_original("threading", "get_native_id")
except AttributeError:
    threading_get_native_id = None

start_new_thread = get_original(six.moves._thread.__name__, "start_new_thread")
thread_get_ident = get_original(six.moves._thread.__name__, "get_ident")
Thread = get_original("threading", "Thread")
Lock = get_original("threading", "Lock")


if is_module_patched("threading"):

    @attr.s
    class DoubleLock(object):
        """A lock that prevent concurrency from a gevent coroutine and from a threading.Thread at the same time."""

        _lock = attr.ib(factory=threading.Lock, init=False, repr=False)
        _thread_lock = attr.ib(factory=Lock, init=False, repr=False)

        def acquire(self):
            # You cannot acquire a gevent-lock from another thread if it has been acquired already:
            # make sure we exclude the gevent-lock from being acquire by another thread by using a thread-lock first.
            self._thread_lock.acquire()
            self._lock.acquire()

        def release(self):
            self._lock.release()
            self._thread_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.release()


else:
    DoubleLock = threading.Lock
