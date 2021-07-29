from __future__ import absolute_import

import threading

from ddtrace.internal import nogevent


cpdef get_thread_name(thread_id):
    # This is a special case for gevent:
    # When monkey patching, gevent replaces all active threads by their greenlet equivalent.
    # This means there's no chance to find the MainThread in the list of _active threads.
    # Therefore we special case the MainThread that way.
    # If native threads are started using gevent.threading, they will be inserted in threading._active
    # so we will find them normally.
    if thread_id == nogevent.main_thread_id:
        return "MainThread"

    # We don't want to bother to lock anything here, especially with eventlet involved 😓. We make a best effort to
    # get the thread name; if we fail, it'll just be an anonymous thread because it's either starting or dying.
    try:
        return threading._active[thread_id].name
    except KeyError:
        try:
            return threading._limbo[thread_id].name
        except KeyError:
            return None


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
