"""CPU profiling collector."""
from __future__ import absolute_import

import sys
import typing

import attr
import six

from ddtrace import _threading as ddtrace_threading
from ddtrace import context
from ddtrace import span as ddspan
from ddtrace.internal import compat
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.utils import attr as attr_utils
from ddtrace.internal.utils import formats
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import traceback
from ddtrace.profiling.collector import stack_event
from ddtrace.profiling.collector import threadtime
from ddtrace.settings.profiling import config


# These are special features that might not be available depending on your Python version and platform
FEATURES = {
    "cpu-time": threadtime.CPU_TIME,
    "stack-exceptions": False,
    "transparent_events": False,
}

# These are flags indicating the enablement of the profiler.  This is handled at the level of
# a global rather than a passed parameter because this is a time of transition
cdef bint use_libdd = False
cdef bint use_py = True

cdef void set_use_libdd(bint flag):
    global use_libdd
    use_libdd = flag

cdef void set_use_py(bint flag):
    global use_py
    use_py = flag



from cpython.object cimport PyObject


# The head lock (the interpreter mutex) is only exposed in a data structure in Python ≥ 3.7
IF UNAME_SYSNAME != "Windows" and PY_VERSION_HEX >= 0x03070000:
    FEATURES['stack-exceptions'] = True

    from cpython cimport PyInterpreterState
    from cpython cimport PyInterpreterState_Head
    from cpython cimport PyInterpreterState_Next
    from cpython cimport PyInterpreterState_ThreadHead
    from cpython cimport PyThreadState_Next
    from cpython.pythread cimport PY_LOCK_ACQUIRED
    from cpython.pythread cimport PyThread_acquire_lock
    from cpython.pythread cimport PyThread_release_lock
    from cpython.pythread cimport PyThread_type_lock
    from cpython.pythread cimport WAIT_LOCK

    IF PY_VERSION_HEX >= 0x030b0000:
        from cpython.ref cimport Py_XDECREF

    cdef extern from "<Python.h>":
        # This one is provided as an opaque struct from Cython's cpython/pystate.pxd,
        # but we need to access some of its fields so we redefine it here.
        ctypedef struct PyThreadState:
            unsigned long thread_id
            PyObject* frame

        _PyErr_StackItem * _PyErr_GetTopmostException(PyThreadState *tstate)

        IF PY_VERSION_HEX >= 0x030b0000:
            ctypedef struct _PyErr_StackItem:
                PyObject* exc_value
        ELSE:
            ctypedef struct _PyErr_StackItem:
                PyObject* exc_type
                PyObject* exc_value
                PyObject* exc_traceback

        PyObject* PyException_GetTraceback(PyObject* exc)
        PyObject* Py_TYPE(PyObject* ob)

    IF PY_VERSION_HEX < 0x03080000:
        # Python 3.7
        cdef extern from "<internal/pystate.h>":

            cdef struct pyinterpreters:
                PyThread_type_lock mutex

            ctypedef struct _PyRuntimeState:
                pyinterpreters interpreters

            cdef extern _PyRuntimeState _PyRuntime

    ELIF PY_VERSION_HEX >= 0x03080000:
        # Python 3.8
        cdef extern from "<internal/pycore_pystate.h>":

            cdef struct pyinterpreters:
                PyThread_type_lock mutex

            ctypedef struct _PyRuntimeState:
                pyinterpreters interpreters

            cdef extern _PyRuntimeState _PyRuntime

        IF PY_VERSION_HEX >= 0x03090000:
            # Needed for accessing _PyGC_FINALIZED when we build with -DPy_BUILD_CORE
            cdef extern from "<internal/pycore_gc.h>":
                pass
            cdef extern from "<Python.h>":
                PyObject* PyThreadState_GetFrame(PyThreadState* tstate)
ELSE:
    from cpython.ref cimport Py_DECREF

    cdef extern from "<pystate.h>":
        PyObject* _PyThread_CurrentFrames()


cdef collect_threads(thread_id_ignore_list, thread_time, thread_span_links) with gil:
    cdef dict current_exceptions = {}

    IF UNAME_SYSNAME != "Windows" and PY_VERSION_HEX >= 0x03070000:
        cdef PyInterpreterState* interp
        cdef PyThreadState* tstate
        cdef _PyErr_StackItem* exc_info
        cdef PyThread_type_lock lmutex = _PyRuntime.interpreters.mutex
        cdef PyObject* exc_type
        cdef PyObject* exc_tb
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
                        # Python 3.11 moved PyFrameObject to internal C API and cannot be directly accessed from tstate
                        IF PY_VERSION_HEX >= 0x030b0000:
                            frame = PyThreadState_GetFrame(tstate)
                            if frame:
                                running_threads[tstate.thread_id] = <object>frame
                            exc_info = _PyErr_GetTopmostException(tstate)
                            if exc_info and exc_info.exc_value and <object> exc_info.exc_value is not None:
                                # Python 3.11 removed exc_type, exc_traceback from exception representations,
                                # can instead derive exc_type and exc_traceback from remaining exc_value field
                                exc_type = Py_TYPE(exc_info.exc_value)
                                exc_tb = PyException_GetTraceback(exc_info.exc_value)
                                if exc_tb:
                                    current_exceptions[tstate.thread_id] = (<object>exc_type, <object>exc_tb)
                                Py_XDECREF(exc_tb)
                            Py_XDECREF(frame)
                        ELSE:
                            frame = tstate.frame
                            if frame:
                                running_threads[tstate.thread_id] = <object>frame
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
            _threading.get_thread_name(pthread_id),
            running_threads[pthread_id],
            current_exceptions.get(pthread_id),
            thread_span_links.get_active_span_from_thread_id(pthread_id) if thread_span_links else None,
            cpu_time,
        )
        for (pthread_id, native_thread_id), cpu_time in cpu_times.items()
        if pthread_id not in thread_id_ignore_list
    )


cdef stack_collect(ignore_profiler, thread_time, max_nframes, interval, wall_time, thread_span_links, collect_endpoint):
    # Do not use `threading.enumerate` to not mess with locking (gevent!)
    thread_id_ignore_list = {
        thread_id
        for thread_id, thread in ddtrace_threading._active.items()
        if getattr(thread, "_ddtrace_profiling_ignore", False)
    } if ignore_profiler else set()

    running_threads = collect_threads(thread_id_ignore_list, thread_time, thread_span_links)

    if thread_span_links:
        # FIXME also use native thread id
        thread_span_links.clear_threads(set(thread[0] for thread in running_threads))

    stack_events = []
    exc_events = []

    for thread_id, thread_native_id, thread_name, thread_pyframes, exception, span, cpu_time in running_threads:
        if thread_name is None:
            # A Python thread with no name is likely still initialising so we
            # ignore it to avoid reporting potentially misleading data.
            # Effectively we would be discarding a negligible number of samples.
            continue

        tasks = _task.list_tasks(thread_id)

        # Inject wall time for all running tasks
        for task_id, task_name, task_pyframes in tasks:

            # Ignore tasks with no frames; nothing to show.
            if task_pyframes is None:
                continue

            frames, nframes = traceback.pyframe_to_frames(task_pyframes, max_nframes)

            if use_libdd and nframes:
                ddup.start_sample(nframes)
                ddup.push_walltime(wall_time, 1)
                ddup.push_threadinfo(thread_id, thread_native_id, thread_name)
                ddup.push_task_id(task_id)
                ddup.push_task_name(task_name)
                ddup.push_class_name(frames[0].class_name)
                for frame in frames:
                    ddup.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                ddup.flush_sample()

            if use_py and nframes:
                stack_events.append(
                    stack_event.StackSampleEvent(
                        thread_id=thread_id,
                        thread_native_id=thread_native_id,
                        thread_name=thread_name,
                        task_id=task_id,
                        task_name=task_name,
                        nframes=nframes, frames=frames,
                        wall_time_ns=wall_time,
                        sampling_period=int(interval * 1e9),
                    )
                )

        frames, nframes = traceback.pyframe_to_frames(thread_pyframes, max_nframes)

        if use_libdd and nframes:
            ddup.start_sample(nframes)
            ddup.push_cputime(cpu_time, 1)
            ddup.push_walltime(wall_time, 1)
            ddup.push_threadinfo(thread_id, thread_native_id, thread_name)
            ddup.push_class_name(frames[0].class_name)
            for frame in frames:
                ddup.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
            ddup.push_span(span, collect_endpoint)
            ddup.flush_sample()

        if use_py and nframes:
            event = stack_event.StackSampleEvent(
                thread_id=thread_id,
                thread_native_id=thread_native_id,
                thread_name=thread_name,
                task_id=None,
                task_name=None,
                nframes=nframes,
                frames=frames,
                wall_time_ns=wall_time,
                cpu_time_ns=cpu_time,
                sampling_period=int(interval * 1e9),
            )
            event.set_trace_info(span, collect_endpoint)
            stack_events.append(event)

        if exception is not None:
            exc_type, exc_traceback = exception

            frames, nframes = traceback.traceback_to_frames(exc_traceback, max_nframes)

            if use_libdd and nframes:
                ddup.start_sample(nframes)
                ddup.push_threadinfo(thread_id, thread_native_id, thread_name)
                ddup.push_exceptioninfo(exc_type, 1)
                ddup.push_class_name(frames[0].class_name)
                for frame in frames:
                    ddup.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                ddup.push_span(span, collect_endpoint)
                ddup.flush_sample()

            if use_py and nframes:
                exc_event = stack_event.StackExceptionSampleEvent(
                    thread_id=thread_id,
                    thread_name=thread_name,
                    thread_native_id=thread_native_id,
                    task_id=None,
                    task_name=None,
                    nframes=nframes,
                    frames=frames,
                    sampling_period=int(interval * 1e9),
                    exc_type=exc_type,
                )
                exc_event.set_trace_info(span, collect_endpoint)
                exc_events.append(exc_event)

    return stack_events, exc_events


if typing.TYPE_CHECKING:
    _thread_span_links_base = _threading._ThreadLink[ddspan.Span]
else:
    _thread_span_links_base = _threading._ThreadLink


@attr.s(slots=True, eq=False)
class _ThreadSpanLinks(_thread_span_links_base):

    def link_span(
            self,
            span # type: typing.Optional[typing.Union[context.Context, ddspan.Span]]
    ):
        # type: (...) -> None
        """Link a span to its running environment.

        Track threads, tasks, etc.
        """
        # Since we're going to iterate over the set, make sure it's locked
        if isinstance(span, ddspan.Span):
            self.link_object(span)

    def get_active_span_from_thread_id(
            self,
            thread_id # type: int
    ):
        # type: (...) -> typing.Optional[ddspan.Span]
        """Return the latest active span for a thread.

        :param thread_id: The thread id.
        :return: A set with the active spans.
        """
        active_span = self.get_object(thread_id)
        if active_span is not None and not active_span.finished:
            return active_span
        return None


def _default_min_interval_time():
    if six.PY2:
        return 0.01
    return sys.getswitchinterval() * 2


@attr.s(slots=True)
class StackCollector(collector.PeriodicCollector):
    """Execution stacks collector."""
    # This need to be a real OS thread in order to catch
    _real_thread = True
    _interval = attr.ib(factory=_default_min_interval_time, init=False, repr=False)
    # This is the minimum amount of time the thread will sleep between polling interval,
    # no matter how fast the computer is.
    min_interval_time = attr.ib(factory=_default_min_interval_time, init=False)

    max_time_usage_pct = attr.ib(type=float, default=config.max_time_usage_pct)
    nframes = attr.ib(type=int, default=config.max_frames)
    ignore_profiler = attr.ib(type=bool, default=config.ignore_profiler)
    endpoint_collection_enabled = attr.ib(default=None)
    tracer = attr.ib(default=None)
    _thread_time = attr.ib(init=False, repr=False, eq=False)
    _last_wall_time = attr.ib(init=False, repr=False, eq=False, type=int)
    _thread_span_links = attr.ib(default=None, init=False, repr=False, eq=False)

    @max_time_usage_pct.validator
    def _check_max_time_usage(self, attribute, value):
        if value <= 0 or value > 100:
            raise ValueError("Max time usage percent must be greater than 0 and smaller or equal to 100")

    def _init(self):
        # type: (...) -> None
        self._thread_time = threadtime._ThreadTime()
        self._last_wall_time = compat.monotonic_ns()
        if self.tracer is not None:
            self._thread_span_links = _ThreadSpanLinks()
            self.tracer.context_provider._on_activate(self._thread_span_links.link_span)
        set_use_libdd(config.export.libdd_enabled)
        set_use_py(config.export.py_enabled)

    def _start_service(self):
        # type: (...) -> None
        # This is split in its own function to ease testing
        self._init()
        super(StackCollector, self)._start_service()

    def _stop_service(self):
        # type: (...) -> None
        super(StackCollector, self)._stop_service()
        if self.tracer is not None:
            self.tracer.context_provider._deregister_on_activate(self._thread_span_links.link_span)

    def _compute_new_interval(self, used_wall_time_ns):
        interval = (used_wall_time_ns / (self.max_time_usage_pct / 100.0)) - used_wall_time_ns
        return max(interval / 1e9, self.min_interval_time)

    def collect(self):
        # Compute wall time
        now = compat.monotonic_ns()
        wall_time = now - self._last_wall_time
        self._last_wall_time = now

        all_events = stack_collect(
            self.ignore_profiler,
            self._thread_time,
            self.nframes,
            self.interval,
            wall_time,
            self._thread_span_links,
            self.endpoint_collection_enabled,
        )

        used_wall_time_ns = compat.monotonic_ns() - now
        self.interval = self._compute_new_interval(used_wall_time_ns)

        return all_events
