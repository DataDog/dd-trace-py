from __future__ import absolute_import

import threading

from ddtrace.profiling import _nogevent
from ddtrace.profiling import _periodic


cpdef get_thread_name(thread_id):
    # This is a special case for gevent:
    # When monkey patching, gevent replaces all active threads by their greenlet equivalent.
    # This means there's no chance to find the MainThread in the list of _active threads.
    # Therefore we special case the MainThread that way.
    # If native threads are started using gevent.threading, they will be inserted in threading._active
    # so we will find them normally.
    if thread_id == _nogevent.main_thread_id:
        return "MainThread"

    # Try to look if this is one of our periodic threads
    try:
        return _periodic.PERIODIC_THREADS[thread_id].name
    except KeyError:
        # Since we own the GIL, we can safely run this and assume no change will happen,
        # without bothering to lock anything
        try:
            return threading._active[thread_id].name
        except KeyError:
            try:
                return threading._limbo[thread_id].name
            except KeyError:
                return "Anonymous Thread %d" % thread_id


cpdef get_thread_native_id(thread_id):
    try:
        thread_obj = threading._active[thread_id]
    except KeyError:
        # This should not happen, unless somebody started a thread without
        # using the `threading` module.
        # In that case, well… just use the thread_id as native_id 🤞
        return thread_id
    else:
        # We prioritize using native ids since we expect them to be surely unique for a program. This is less true
        # for hashes since they are relative to the memory address which can easily be the same across different
        # objects.
        try:
            return thread_obj.native_id
        except AttributeError:
            # Python < 3.8
            return hash(thread_obj)
