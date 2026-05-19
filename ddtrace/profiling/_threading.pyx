from __future__ import absolute_import

import sys

from ddtrace.internal._threads import periodic_threads
from ddtrace.internal._unpatched import _threading as ddtrace_threading


cpdef get_thread_by_id(thread_id):
    # Look for all threads, including the ones we create
    # We don't want to bother to lock anything here, especially with
    # eventlet involved (sad-sweat-emoji). We make a best effort to get the thread name; if
    # we fail, it'll just be an anonymous thread because it's either
    # starting or dying.
    try:
        return ddtrace_threading._active[thread_id]
    except (KeyError, AttributeError):
        try:
            return ddtrace_threading._limbo[thread_id]
        except (KeyError, AttributeError):
            pass

    return None


cpdef get_thread_name(thread_id):
    try:
        return periodic_threads[thread_id].name
    except KeyError:
        thread = get_thread_by_id(thread_id)
        return thread.name if thread is not None else None


cpdef get_thread_native_id(thread_id):
    thread = get_thread_by_id(thread_id)

    # _DummyThread lacks _native_id, so thread.native_id raises
    # AttributeError.  Fall back to thread_id.
    # Also handles thread being None (not found).
    try:
        return thread.native_id
    except AttributeError:
        return thread_id
