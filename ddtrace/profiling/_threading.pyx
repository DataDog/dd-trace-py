from __future__ import absolute_import

import sys

from ddtrace.internal._threads import periodic_threads
from ddtrace.internal._unpatched import _threading as ddtrace_threading


cpdef object get_thread_by_id(int thread_id):
    # Look for all threads, including the ones we create.
    # Geting the thread name is best-effort;
    # failing means missing an anonymous thread that's either starting or dying.

    cdef object active_thread
    cdef object limbo_thread

    active_thread = getattr(ddtrace_threading, '_active', {}).get(thread_id)
    if active_thread:
        return active_thread

    limbo_thread = getattr(ddtrace_threading, '_limbo', {}).get(thread_id)
    if limbo_thread:
        return limbo_thread

    return None


cpdef object get_thread_name(int thread_id):
    cdef object thread
    try:
        return periodic_threads[thread_id].name
    except KeyError:
        thread = get_thread_by_id(thread_id)
        return thread.name if thread is not None else None


cpdef int get_thread_native_id(int thread_id):
    cdef object thread = get_thread_by_id(thread_id)

    # _DummyThread (used by gevent greenlets) lacks _native_id, so
    # thread.native_id raises AttributeError.  Fall back to thread_id.
    # Also handles thread being None (not found).
    try:
        return thread.native_id
    except AttributeError:
        return thread_id
