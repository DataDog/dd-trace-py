# -*- encoding: utf-8 -*-
"""This files exposes non-gevent Python original functions."""
import threading

import attr
import six

from ddtrace.internal import compat
from ddtrace.internal import forksafe


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

if six.PY2 and is_module_patched("threading"):
    _allocate_lock = get_original("threading", "_allocate_lock")
    _threading_RLock = get_original("threading", "_RLock")
    _threading_Verbose = get_original("threading", "_Verbose")

    class _RLock(_threading_RLock):
        """Patched RLock to ensure threading._allocate_lock is called rather than
        gevent.threading._allocate_lock if patching has occurred. This is not
        necessary in Python 3 where the RLock function uses the _CRLock so is
        unaffected by gevent patching.
        """

        def __init__(self, verbose=None):
            # We want to avoid calling the RLock init as it will allocate a gevent lock
            # That means we have to reproduce the code from threading._RLock.__init__ here
            # https://github.com/python/cpython/blob/8d21aa21f2cbc6d50aab3f420bb23be1d081dac4/Lib/threading.py#L132-L136
            _threading_Verbose.__init__(self, verbose)
            self.__block = _allocate_lock()
            self.__owner = None
            self.__count = 0

    def RLock(*args, **kwargs):
        return _RLock(*args, **kwargs)


else:
    # We do not patch RLock in Python 3 however for < 3.7 the C implementation of
    # RLock might not be available as the _thread module is optional.  In that
    # case, the Python implementation will be used. This means there is still
    # the possibility that RLock in Python 3 will cause problems for gevent with
    # ddtrace profiling enabled though it remains an open question when that
    # would be the case for the supported platforms.
    # https://github.com/python/cpython/blob/c19983125a42a4b4958b11a26ab5e03752c956fc/Lib/threading.py#L38-L41
    # https://github.com/python/cpython/blob/c19983125a42a4b4958b11a26ab5e03752c956fc/Doc/library/_thread.rst#L26-L27
    RLock = get_original("threading", "RLock")


is_threading_patched = is_module_patched("threading")

if is_threading_patched:

    @attr.s
    class DoubleLock(object):
        """A lock that prevent concurrency from a gevent coroutine and from a threading.Thread at the same time."""

        # This is a gevent-patched threading.Lock (= a gevent Lock)
        _lock = attr.ib(factory=forksafe.Lock, init=False, repr=False)
        # This is a unpatched threading.Lock (= a real threading.Lock)
        _thread_lock = attr.ib(factory=lambda: forksafe.ResetObject(Lock), init=False, repr=False)

        def acquire(self):
            # type: () -> None
            # You cannot acquire a gevent-lock from another thread if it has been acquired already:
            # make sure we exclude the gevent-lock from being acquire by another thread by using a thread-lock first.
            self._thread_lock.acquire()
            self._lock.acquire()

        def release(self):
            # type: () -> None
            self._lock.release()
            self._thread_lock.release()

        def __enter__(self):
            # type: () -> DoubleLock
            self.acquire()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.release()


else:
    DoubleLock = threading.Lock  # type:  ignore[misc,assignment]


if is_threading_patched:
    # NOTE: bold assumption: this module is always imported by the MainThread.
    # The python `threading` module makes that assumption and it's beautiful we're going to do the same.
    # We don't have the choice has we can't access the original MainThread
    main_thread_id = thread_get_ident()
else:
    main_thread_id = compat.main_thread.ident
