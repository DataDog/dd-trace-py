from __future__ import absolute_import

from ddtrace.internal._threads import periodic_threads
from ddtrace.internal._unpatched import _threading as ddtrace_threading


cpdef get_thread_by_id(thread_id):
    # Look for all threads, including the ones we create
    # We don't want to bother to lock anything here, especially with
    # eventlet involved (sad-sweat-emoji). We make a best effort to get the thread name; if
    # we fail, it'll just be an anonymous thread because it's either
    # starting or dying.
    _active = getattr(ddtrace_threading, "_active", None)
    if _active is not None:
        thread = _active.get(thread_id)
        if thread is not None:
            return thread

    _limbo = getattr(ddtrace_threading, "_limbo", None)
    if _limbo is not None:
        thread = _limbo.get(thread_id)
        if thread is not None:
            return thread

    return None


cpdef get_thread_name(thread_id):
    pt = periodic_threads.get(thread_id)
    if pt is not None:
        return pt.name
    thread = get_thread_by_id(thread_id)
    return thread.name if thread is not None else None


cpdef get_thread_native_id(thread_id):
    thread = get_thread_by_id(thread_id)

    # _DummyThread lacks _native_id, so thread.native_id raises
    # AttributeError.  Fall back to thread_id.
    # Also handles thread being None (not found).
    return getattr(thread, "native_id", thread_id)
