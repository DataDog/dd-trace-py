"""CPU profiling collector."""
from __future__ import absolute_import

import collections
import logging
import sys
import threading
import weakref

from ddtrace import compat
from ddtrace.profiling import _attr
from ddtrace.profiling import _periodic
from ddtrace.profiling import _nogevent
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.profiling.collector import _traceback
from ddtrace.utils import formats
from ddtrace.vendor import attr
from ddtrace.vendor import six


_LOG = logging.getLogger(__name__)


if _nogevent.is_module_patched("threading"):
    # NOTE: bold assumption: this module is always imported by the MainThread.
    # The python `threading` module makes that assumption and it's beautiful we're going to do the same.
    # We don't have the choice has we can't access the original MainThread
    _main_thread_id = _nogevent.thread_get_ident()
else:
    from ddtrace.vendor.six.moves._thread import get_ident as _thread_get_ident
    if six.PY2:
        _main_thread_id = threading._MainThread().ident
    else:
        _main_thread_id = threading.main_thread().ident


# NOTE: Do not use LOG here. This code runs under a real OS thread and is unable to acquire any lock of the `logging`
# module without having gevent crashing our dedicated thread.


# Those are special features that might not be available depending on your Python version and platform
FEATURES = {
    "cpu-time": False,
    "stack-exceptions": False,
}

IF UNAME_SYSNAME == "Linux":
    FEATURES['cpu-time'] = True

    from posix.time cimport timespec, clock_gettime
    from posix.types cimport clockid_t
    from cpython.exc cimport PyErr_SetFromErrno

    cdef extern from "<pthread.h>":
        # POSIX says this might be a struct, but CPython relies on it being an unsigned long.
        # We should be defining pthread_t here like this:
        # ctypedef unsigned long pthread_t
        # but e.g. musl libc defines pthread_t as a struct __pthread * which breaks the arithmetic Cython
        # wants to do.
        # We pay this with a warning at compilation time, but it works anyhow.
        int pthread_getcpuclockid(unsigned long thread, clockid_t *clock_id)

    cdef p_pthread_getcpuclockid(tid):
        cdef clockid_t clock_id
        if pthread_getcpuclockid(tid, &clock_id) == 0:
            return clock_id
        PyErr_SetFromErrno(OSError)

    # Python < 3.3 does not have `time.clock_gettime`
    cdef p_clock_gettime_ns(clk_id):
        cdef timespec tp
        if clock_gettime(clk_id, &tp) == 0:
            return int(tp.tv_nsec + tp.tv_sec * 10e8)
        PyErr_SetFromErrno(OSError)

    cdef class _ThreadTime(object):
        cdef dict _last_thread_time

        def __init__(self):
            # This uses a tuple of (pthread_id, thread_native_id) as the key to identify the thread: you'd think using
            # the pthread_t id would be enough, but the glibc reuses the id.
            self._last_thread_time = {}

        # Only used in tests
        def _get_last_thread_time(self):
            return dict(self._last_thread_time)

        def __call__(self, pthread_ids):
            cdef list cpu_times = []
            for pthread_id in pthread_ids:
                # TODO: Use QueryThreadCycleTime on Windows?
                # ⚠ WARNING ⚠
                # `pthread_getcpuclockid` can make Python segfault if the thread is does not exist anymore.
                # In order avoid this, this function must be called with the GIL being held the entire time.
                # This is why this whole file is compiled down to C: we make sure we never release the GIL between
                # calling sys._current_frames() and pthread_getcpuclockid, making sure no thread disappeared.
                try:
                    cpu_time = p_clock_gettime_ns(p_pthread_getcpuclockid(pthread_id))
                except OSError:
                    # Just in case it fails, set it to 0
                    # (Note that glibc never fails, it segfaults instead)
                    cpu_time = 0
                cpu_times.append(cpu_time)

            cdef dict pthread_cpu_time = {}

            # We should now be safe doing more Pythonic stuff and maybe releasing the GIL
            for pthread_id, cpu_time in zip(pthread_ids, cpu_times):
                thread_native_id = get_thread_native_id(pthread_id)
                key = pthread_id, thread_native_id
                # Do a max(0, …) here just in case the result is < 0:
                # This should never happen, but it can happen if the one chance in a billion happens:
                # - A new thread has been created and has the same native id and the same pthread_id.
                # - We got an OSError with clock_gettime_ns
                pthread_cpu_time[key] = max(0, cpu_time - self._last_thread_time.get(key, cpu_time))
                self._last_thread_time[key] = cpu_time

            # Clear cache
            keys = list(pthread_cpu_time.keys())
            for key in list(self._last_thread_time.keys()):
                if key not in keys:
                    del self._last_thread_time[key]

            return pthread_cpu_time
ELSE:
    cdef class _ThreadTime(object):
        cdef long _last_process_time

        def __init__(self):
            self._last_process_time = compat.process_time_ns()

        def __call__(self, pthread_ids):
            current_process_time = compat.process_time_ns()
            cpu_time = current_process_time - self._last_process_time
            self._last_process_time = current_process_time
            # Spread the consumed CPU time on all threads.
            # It's not fair, but we have no clue which CPU used more unless we can use `pthread_getcpuclockid`
            # Check that we don't have zero thread — _might_ very rarely happen at shutdown
            nb_threads = len(pthread_ids)
            if nb_threads == 0:
                cpu_time = 0
            else:
                cpu_time //= nb_threads
            return {
                (pthread_id, get_thread_native_id(pthread_id)): cpu_time
                for pthread_id in pthread_ids
            }


@event.event_class
class StackBasedEvent(event.SampleEvent):
    thread_id = attr.ib(default=None)
    thread_name = attr.ib(default=None)
    thread_native_id = attr.ib(default=None)
    frames = attr.ib(default=None)
    nframes = attr.ib(default=None)
    trace_ids = attr.ib(default=None)


@event.event_class
class StackSampleEvent(StackBasedEvent):
    """A sample storing executions frames for a thread."""

    # Wall clock
    wall_time_ns = attr.ib(default=0)
    # CPU time in nanoseconds
    cpu_time_ns = attr.ib(default=0)


@event.event_class
class StackExceptionSampleEvent(StackBasedEvent):
    """A a sample storing raised exceptions and their stack frames."""

    exc_type = attr.ib(default=None)

from cpython.object cimport PyObject

# The head lock (the interpreter mutex) is only exposed in a data structure in Python ≥ 3.7
IF UNAME_SYSNAME != "Windows" and PY_MAJOR_VERSION >= 3 and PY_MINOR_VERSION >= 7:
    FEATURES['stack-exceptions'] = True

    from cpython cimport PyInterpreterState
    from cpython cimport PyInterpreterState_Head, PyInterpreterState_Next
    from cpython cimport PyInterpreterState_ThreadHead, PyThreadState_Next

    from cpython.pythread cimport (
        PyThread_acquire_lock, PyThread_release_lock,
        WAIT_LOCK,
        PyThread_type_lock,
        PY_LOCK_ACQUIRED
    )

    cdef extern from "<Python.h>":
        # This one is provided as an opaque struct from Cython's cpython/pystate.pxd,
        # but we need to access some of its fields so we redefine it here.
        ctypedef struct PyThreadState:
            unsigned long thread_id
            PyObject* frame

        _PyErr_StackItem * _PyErr_GetTopmostException(PyThreadState *tstate)

        ctypedef struct _PyErr_StackItem:
            PyObject* exc_type
            PyObject* exc_value
            PyObject* exc_traceback

    IF PY_MINOR_VERSION == 7:
        # Python 3.7
        cdef extern from "<internal/pystate.h>":

            cdef struct pyinterpreters:
                PyThread_type_lock mutex

            ctypedef struct _PyRuntimeState:
                pyinterpreters interpreters

            cdef extern _PyRuntimeState _PyRuntime

    ELIF PY_MINOR_VERSION >= 8:
        # Python 3.8
        cdef extern from "<internal/pycore_pystate.h>":

            cdef struct pyinterpreters:
                PyThread_type_lock mutex

            ctypedef struct _PyRuntimeState:
                pyinterpreters interpreters

            cdef extern _PyRuntimeState _PyRuntime

        IF PY_MINOR_VERSION >= 9:
            # Needed for accessing _PyGC_FINALIZED when we build with -DPy_BUILD_CORE
            cdef extern from "<internal/pycore_gc.h>":
                pass
ELSE:
    from cpython.ref cimport Py_DECREF

    cdef extern from "<pystate.h>":
        PyObject* _PyThread_CurrentFrames()



cdef get_thread_name(thread_id):
    # This is a special case for gevent:
    # When monkey patching, gevent replaces all active threads by their greenlet equivalent.
    # This means there's no chance to find the MainThread in the list of _active threads.
    # Therefore we special case the MainThread that way.
    # If native threads are started using gevent.threading, they will be inserted in threading._active
    # so we will find them normally.
    if thread_id == _main_thread_id:
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


cdef collect_threads(ignore_profiler, thread_time, thread_span_links) with gil:
    cdef dict current_exceptions = {}

    IF UNAME_SYSNAME != "Windows" and PY_MAJOR_VERSION >= 3 and PY_MINOR_VERSION >= 7:
        cdef PyInterpreterState* interp
        cdef PyThreadState* tstate
        cdef _PyErr_StackItem* exc_info
        cdef PyThread_type_lock lmutex = _PyRuntime.interpreters.mutex

        cdef dict running_threads = {}

        # This is an internal lock but we do need it.
        # See https://bugs.python.org/issue1021318
        if PyThread_acquire_lock(lmutex, WAIT_LOCK) == PY_LOCK_ACQUIRED:
            # Do not try to do anything fancy here:
            # Even calling print() will deadlock the program has it will try
            # to lock the GIL and somehow touching this mutex.
            try:
                interp = PyInterpreterState_Head()

                while interp:
                    tstate = PyInterpreterState_ThreadHead(interp)
                    while tstate:
                        # The frame can be NULL
                        if tstate.frame:
                            running_threads[tstate.thread_id] = <object>tstate.frame

                        exc_info = _PyErr_GetTopmostException(tstate)
                        if exc_info and exc_info.exc_type and exc_info.exc_traceback:
                            current_exceptions[tstate.thread_id] = (<object>exc_info.exc_type, <object>exc_info.exc_traceback)

                        tstate = PyThreadState_Next(tstate)

                    interp = PyInterpreterState_Next(interp)
            finally:
                PyThread_release_lock(lmutex)
    ELSE:
        cdef dict running_threads = <dict>_PyThread_CurrentFrames()

        # Now that we own the ref via <dict> casting, we can safely decrease the default refcount
        # so we don't leak the object
        Py_DECREF(running_threads)

    cdef dict cpu_times = thread_time(running_threads.keys())

    return tuple(
        (
            pthread_id,
            native_thread_id,
            get_thread_name(pthread_id),
            running_threads[pthread_id],
            current_exceptions.get(pthread_id),
            thread_span_links.get_active_leaf_spans_from_thread_id(pthread_id) if thread_span_links else set(),
            cpu_time,
        )
        for (pthread_id, native_thread_id), cpu_time in cpu_times.items()
        if not ignore_profiler or pthread_id not in _periodic.PERIODIC_THREADS
    )



cdef stack_collect(ignore_profiler, thread_time, max_nframes, interval, wall_time, thread_span_links):

    running_threads = collect_threads(ignore_profiler, thread_time, thread_span_links)

    if thread_span_links:
        # FIXME also use native thread id
        thread_span_links.clear_threads(tuple(thread[0] for thread in running_threads))

    stack_events = []
    exc_events = []

    for thread_id, thread_native_id, thread_name, frame, exception, spans, cpu_time in running_threads:
        frames, nframes = _traceback.pyframe_to_frames(frame, max_nframes)
        stack_events.append(
            StackSampleEvent(
                thread_id=thread_id,
                thread_native_id=thread_native_id,
                thread_name=thread_name,
                trace_ids=set(span.trace_id for span in spans),
                nframes=nframes, frames=frames,
                wall_time_ns=wall_time,
                cpu_time_ns=cpu_time,
                sampling_period=int(interval * 1e9),
            ),
        )

        if exception is not None:
            exc_type, exc_traceback = exception
            frames, nframes = _traceback.traceback_to_frames(exc_traceback, max_nframes)
            exc_events.append(
                StackExceptionSampleEvent(
                    thread_id=thread_id,
                    thread_name=thread_name,
                    thread_native_id=thread_native_id,
                    nframes=nframes,
                    frames=frames,
                    sampling_period=int(interval * 1e9),
                    exc_type=exc_type,
                ),
            )

    return stack_events, exc_events


@attr.s(slots=True, eq=False)
class _ThreadSpanLinks(object):

    # Keys is a thread_id
    # Value is a set of weakrefs to spans
    _thread_id_to_spans = attr.ib(factory=lambda: collections.defaultdict(set), repr=False, init=False)
    _lock = attr.ib(factory=_nogevent.Lock, repr=False, init=False)

    def link_span(self, span):
        """Link a span to its running environment.

        Track threads, tasks, etc.
        """
        # Since we're going to iterate over the set, make sure it's locked
        with self._lock:
            self._thread_id_to_spans[_nogevent.thread_get_ident()].add(weakref.ref(span))

    def clear_threads(self, existing_thread_ids):
        """Clear the stored list of threads based on the list of existing thread ids.

        If any thread that is part of this list was stored, its data will be deleted.

        :param existing_thread_ids: A set of thread ids to keep.
        """
        with self._lock:
            # Iterate over a copy of the list of keys since it's mutated during our iteration.
            for thread_id in list(self._thread_id_to_spans.keys()):
                if thread_id not in existing_thread_ids:
                    del self._thread_id_to_spans[thread_id]

    def get_active_leaf_spans_from_thread_id(self, thread_id):
        """Return the latest active spans for a thread.

        In theory this should return a single span, though if multiple children span are active without being finished,
        there can be several spans returned.

        :param thread_id: The thread id.
        :return: A set with the active spans.
        """
        alive_spans = set()

        with self._lock:
            span_list = self._thread_id_to_spans.get(thread_id, ())
            dead_spans = set()
            for span_ref in span_list:
                span = span_ref()
                if span is None:
                    dead_spans.add(span_ref)
                else:
                    alive_spans.add(span)

            # Clean the set from the dead spans
            for dead_span in dead_spans:
                span_list.remove(dead_span)

        # Iterate over a copy so we can modify the original
        for span in alive_spans.copy():
            if not span.finished:
                try:
                    alive_spans.remove(span._parent)
                except KeyError:
                    pass

        return {span for span in alive_spans if not span.finished}


@attr.s(slots=True)
class StackCollector(collector.PeriodicCollector):
    """Execution stacks collector."""
    # This is the minimum amount of time the thread will sleep between polling interval,
    # no matter how fast the computer is.
    MIN_INTERVAL_TIME = 0.01    # sleep at least 10 ms

    # This need to be a real OS thread in order to catch
    _real_thread = True
    _interval = attr.ib(default=MIN_INTERVAL_TIME, repr=False)

    max_time_usage_pct = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_TIME_USAGE_PCT", 2, float))
    nframes = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_FRAMES", 64, int))
    ignore_profiler = attr.ib(factory=_attr.from_env("DD_PROFILING_IGNORE_PROFILER", True, formats.asbool))
    tracer = attr.ib(default=None)
    _thread_time = attr.ib(init=False, repr=False)
    _last_wall_time = attr.ib(init=False, repr=False)
    _thread_span_links = attr.ib(default=None, init=False, repr=False)

    @max_time_usage_pct.validator
    def _check_max_time_usage(self, attribute, value):
        if value <= 0 or value > 100:
            raise ValueError("Max time usage percent must be greater than 0 and smaller or equal to 100")

    def _init(self):
        self._thread_time = _ThreadTime()
        self._last_wall_time = compat.monotonic_ns()
        if self.tracer is not None:
            self._thread_span_links = _ThreadSpanLinks()
            self.tracer.on_start_span(self._thread_span_links.link_span)

    def start(self):
        # This is split in its own function to ease testing
        self._init()
        super(StackCollector, self).start()

    def stop(self):
        super(StackCollector, self).stop()
        if self.tracer is not None:
            self.tracer.deregister_on_start_span(self._thread_span_links.link_span)

    def _compute_new_interval(self, used_wall_time_ns):
        interval = (used_wall_time_ns / (self.max_time_usage_pct / 100.0)) - used_wall_time_ns
        return max(interval / 1e9, self.MIN_INTERVAL_TIME)

    def collect(self):
        # Compute wall time
        now = compat.monotonic_ns()
        wall_time = now - self._last_wall_time
        self._last_wall_time = now

        all_events = stack_collect(
            self.ignore_profiler, self._thread_time, self.nframes, self.interval, wall_time, self._thread_span_links,
        )

        used_wall_time_ns = compat.monotonic_ns() - now
        self.interval = self._compute_new_interval(used_wall_time_ns)

        return all_events
