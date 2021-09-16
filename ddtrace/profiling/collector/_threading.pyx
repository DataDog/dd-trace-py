from __future__ import absolute_import

import threading

from ddtrace.internal import nogevent
from ddtrace.vendor import wrapt


IF UNAME_SYSNAME == "Darwin":
    from cpython cimport *

    ctypedef int          kern_return_t
    ctypedef void*        ipc_space_t
    ctypedef ipc_space_t  mach_port_t
    ctypedef unsigned int mach_port_name_t
    ctypedef unsigned int integer_t
    ctypedef unsigned int mach_msg_type_number_t
    ctypedef int          policy_t
    ctypedef void*        thread_port_t
    ctypedef unsigned int natural_t
    ctypedef natural_t    thread_flavor_t

    cdef extern from "mach/time_value.h":
        ctypedef struct time_value_t:
            integer_t seconds
            integer_t microseconds

    cdef extern from "mach/thread_info.h":
        ctypedef struct thread_basic_info_data_t:
            time_value_t    user_time
            time_value_t    system_time
            integer_t       cpu_usage
            policy_t        policy
            integer_t       run_state
            integer_t       flags
            integer_t       suspend_count
            integer_t       sleep_time

        ctypedef thread_basic_info_data_t* thread_info_t

    cdef extern from "mach/mach_init.h":
        thread_port_t mach_thread_self()

    cdef extern from "mach/mach_port.h":
        kern_return_t mach_port_deallocate(ipc_space_t, mach_port_name_t)

    cdef extern from "mach/mach_traps.h":
        mach_port_t mach_task_self()

    cdef extern from "mach/thread_act.h":
        kern_return_t thread_info(thread_port_t, thread_flavor_t, thread_info_t, mach_msg_type_number_t*)

    KERN_SUCCESS = 0
    THREAD_BASIC_INFO = 3

    _OriginalThread = threading.Thread

    class _ThreadProxy(wrapt.ObjectProxy):
        def __init__(self, thread):
            super(_ThreadProxy, self).__init__(thread)
            self._thread_port = PyLong_FromLong(<long>mach_thread_self())
            self._thread_time_ms = None
            self._original_run = self.run

        def _run(self):
            mach_port_deallocate(mach_task_self(), self._thread_port)
            self._thread_port = PyLong_FromLong(<long>mach_thread_self())
            threading._active[threading.get_ident()] = self
            return self._original_run()

        def _thread_time(self):
            cdef thread_basic_info_data_t info
            cdef mach_msg_type_number_t   count

            if KERN_SUCCESS == thread_info(<thread_port_t>PyLong_AsLong(self._thread_port), THREAD_BASIC_INFO, &info, &count):
                return (
                    (info.user_time.seconds + info.system_time.seconds) * 1000000
                    + info.user_time.microseconds + info.system_time.microseconds
                )
            
            return None

        def _last_thread_time(self):
            if self._thread_time_ms is None:
                self._thread_time_ms = self._thread_time()
                return 0

            current_time_ms = self._thread_time()
            try:
                return (current_time_ms - self._thread_time_ms) * 1000
            finally:
                self._thread_time_ms = current_time_ms

        def start(self):
            self.run = self._run
            return self.__wrapped__.start()

        def __del__(self):
            mach_port_deallocate(mach_task_self(), self._thread_port)
            try:
                self.__wrapped__.__del__()
            except AttributeError:
                pass


    class _Thread(object):
        def __new__(cls, *args, **kwargs):
            return _ThreadProxy(_OriginalThread(*args, **kwargs))

    # DEV: We wrap all threads but this is only meaningful for the main thread
    # as mach_thread_self() needs to be called within the thread itself.
    # Ideally we would want to patch `threading.Thread` before any additional
    # threads are created.
    threading.Thread = _Thread
    threading._active = {ident: _ThreadProxy(thread) for ident, thread in threading._active.items()}
    threading._limbo = {ident: _ThreadProxy(thread) for ident, thread in threading._limbo.items()}


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
