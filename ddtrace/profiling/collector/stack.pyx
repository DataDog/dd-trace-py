"""CPU profiling collector."""
from __future__ import absolute_import

from itertools import chain
import logging
import sys
import time
import typing

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace._trace import context
from ddtrace._trace import span as ddspan
from ddtrace.trace import Tracer
from ddtrace.internal._threads import periodic_threads
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling import stack_v2
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import _traceback
from ddtrace.profiling.collector import threading
from ddtrace.settings.profiling import config


LOG = logging.getLogger(__name__)


# These are special features that might not be available depending on your Python version and platform
FEATURES = {
    "cpu-time": False,
    "stack-exceptions": True,
    "transparent_events": False,
}

IF UNAME_SYSNAME == "Linux":
    FEATURES['cpu-time'] = True

    from posix.time cimport clock_gettime
    from posix.time cimport timespec
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
                thread_native_id = _threading.get_thread_native_id(pthread_id)
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
    from libc cimport stdint

    cdef class _ThreadTime(object):
        cdef stdint.int64_t _last_process_time

        def __init__(self):
            self._last_process_time = time.process_time_ns()

        def __call__(self, pthread_ids):
            current_process_time = time.process_time_ns()
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
                (pthread_id, _threading.get_thread_native_id(pthread_id)): cpu_time
                for pthread_id in pthread_ids
            }


from cpython.object cimport PyObject
from cpython.ref cimport Py_DECREF

cdef extern from "<pystate.h>":
    PyObject* _PyThread_CurrentFrames()

IF 0x030b0000 <= PY_VERSION_HEX < 0x30d0000:
    cdef extern from "<pystate.h>":
        PyObject* _PyThread_CurrentExceptions()

ELIF UNAME_SYSNAME != "Windows":
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

        PyObject* PyException_GetTraceback(PyObject* exc)
        PyObject* Py_TYPE(PyObject* ob)

    IF PY_VERSION_HEX >= 0x03080000:
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
    FEATURES['stack-exceptions'] = False


cdef collect_threads(thread_id_ignore_list, thread_time, thread_span_links) with gil:
    cdef dict running_threads = <dict>_PyThread_CurrentFrames()
    Py_DECREF(running_threads)

    IF PY_VERSION_HEX >= 0x030b0000:
        IF PY_VERSION_HEX >= 0x030d0000:
            current_exceptions = sys._current_exceptions()
        ELSE:
            cdef dict current_exceptions = <dict>_PyThread_CurrentExceptions()
            Py_DECREF(current_exceptions)

        for thread_id, exc_info in current_exceptions.items():
            if exc_info is None:
                continue
            IF PY_VERSION_HEX >= 0x030c0000:
                exc_type = type(exc_info)
                exc_traceback = getattr(exc_info, "__traceback__", None)
            ELSE:
                exc_type, exc_value, exc_traceback = exc_info
            current_exceptions[thread_id] = exc_type, exc_traceback

    ELIF UNAME_SYSNAME != "Windows":
        cdef PyInterpreterState* interp
        cdef PyThreadState* tstate
        cdef _PyErr_StackItem* exc_info
        cdef PyThread_type_lock lmutex = _PyRuntime.interpreters.mutex
        cdef PyObject* exc_type
        cdef PyObject* exc_tb
        cdef dict current_exceptions = {}

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
                        exc_info = _PyErr_GetTopmostException(tstate)
                        if exc_info and exc_info.exc_type and exc_info.exc_traceback:
                            current_exceptions[tstate.thread_id] = (<object>exc_info.exc_type, <object>exc_info.exc_traceback)
                        tstate = PyThreadState_Next(tstate)

                    interp = PyInterpreterState_Next(interp)
            finally:
                PyThread_release_lock(lmutex)
    ELSE:
        cdef dict current_exceptions = {}

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


cdef stack_collect(ignore_profiler, thread_time, max_nframes, interval, wall_time, thread_span_links, collect_endpoint, now_ns = 0):
    # Do not use `threading.enumerate` to not mess with locking (gevent!)
    # Also collect the native threads, that are not registered with the built-in
    # threading module, to keep backward compatibility with the previous
    # pure-Python implementation of periodic threads.
    thread_id_ignore_list = {
        thread_id
        for thread_id, thread in chain(periodic_threads.items(), ddtrace_threading._active.items())
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

            frames, nframes = _traceback.pyframe_to_frames(task_pyframes, max_nframes)

            if nframes:
                handle = ddup.SampleHandle()
                handle.push_monotonic_ns(now_ns)
                handle.push_walltime(wall_time, 1)
                handle.push_threadinfo(thread_id, thread_native_id, thread_name)
                handle.push_task_id(task_id)
                handle.push_task_name(task_name)
                handle.push_class_name(frames[0].class_name)
                for frame in frames:
                    handle.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                handle.flush_sample()

        frames, nframes = _traceback.pyframe_to_frames(thread_pyframes, max_nframes)

        if nframes:
            handle = ddup.SampleHandle()
            handle.push_monotonic_ns(now_ns)
            handle.push_cputime( cpu_time, 1)
            handle.push_walltime( wall_time, 1)
            handle.push_threadinfo(thread_id, thread_native_id, thread_name)
            handle.push_class_name(frames[0].class_name)
            for frame in frames:
                handle.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
            handle.push_span(span)
            handle.flush_sample()

        if exception is not None:
            exc_type, exc_traceback = exception

            frames, nframes = _traceback.traceback_to_frames(exc_traceback, max_nframes)

            if nframes:
                handle = ddup.SampleHandle()
                handle.push_monotonic_ns(now_ns)
                handle.push_threadinfo(thread_id, thread_native_id, thread_name)
                handle.push_exceptioninfo(exc_type, 1)
                handle.push_class_name(frames[0].class_name)
                for frame in frames:
                    handle.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                handle.push_span(span)
                handle.flush_sample()

    return stack_events, exc_events


if typing.TYPE_CHECKING:
    _thread_span_links_base = _threading._ThreadLink[ddspan.Span]
else:
    _thread_span_links_base = _threading._ThreadLink


class _ThreadSpanLinks(_thread_span_links_base):

    __slots__ = ()

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
    return sys.getswitchinterval() * 2


class StackCollector(collector.PeriodicCollector):
    """Execution stacks collector."""

    __slots__ = (
        "_real_thread",
        "min_interval_time",
        "max_time_usage_pct",
        "nframes",
        "ignore_profiler",
        "endpoint_collection_enabled",
        "tracer",
        "_thread_time",
        "_last_wall_time",
        "_thread_span_links",
        "_stack_collector_v2_enabled",
    )

    def __init__(self,
                 max_time_usage_pct: float = config.max_time_usage_pct,
                 nframes: int = config.max_frames,
                 ignore_profiler: bool = config.ignore_profiler,
                 endpoint_collection_enabled: typing.Optional[bool] = None,
                 tracer: typing.Optional[Tracer] = None,
                 _stack_collector_v2_enabled: bool = config.stack.v2_enabled):
        super().__init__(interval= _default_min_interval_time())
        if max_time_usage_pct <= 0 or max_time_usage_pct > 100:
            raise ValueError("Max time usage percent must be greater than 0 and smaller or equal to 100")

        # This need to be a real OS thread in order to catch
        self._real_thread: bool = True
        self.min_interval_time: float = _default_min_interval_time()

        self.max_time_usage_pct: float = max_time_usage_pct
        self.nframes: int = nframes
        self.ignore_profiler: bool = ignore_profiler
        self.endpoint_collection_enabled: typing.Optional[bool] = endpoint_collection_enabled
        self.tracer: typing.Optional[Tracer] = tracer
        self._thread_time: typing.Optional[_ThreadTime] = None
        self._last_wall_time: int = 0  # Placeholder for initial value
        self._thread_span_links: typing.Optional[_ThreadSpanLinks] = None
        self._stack_collector_v2_enabled: bool = _stack_collector_v2_enabled


    def __repr__(self):
        class_name = self.__class__.__name__
        attrs = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        attrs_str = ", ".join(f"{k}={v!r}" for k, v in attrs.items())

        slot_attrs = {slot: getattr(self, slot) for slot in self.__slots__ if not slot.startswith("_")}
        slot_attrs_str = ", ".join(f"{k}={v!r}" for k, v in slot_attrs.items())

        return f"{class_name}({attrs_str}, {slot_attrs_str})"


    def _init(self):
        # type: (...) -> None
        self._thread_time = _ThreadTime()
        self._last_wall_time = time.monotonic_ns()
        if self.tracer is not None:
            self._thread_span_links = _ThreadSpanLinks()
            link_span = stack_v2.link_span if self._stack_collector_v2_enabled else self._thread_span_links.link_span
            self.tracer.context_provider._on_activate(link_span)

        # If stack v2 is enabled, then use the v2 sampler
        if self._stack_collector_v2_enabled:
            # stack v2 requires us to patch the Threading module.  It's possible to do this from the stack v2 code
            # itself, but it's a little bit fiddly and it's easier to make it correct here.
            # TODO take the `threading` import out of here and just handle it in v2 startup
            threading.init_stack_v2()
            stack_v2.set_adaptive_sampling(config.stack.v2_adaptive_sampling)
            stack_v2.start()

    def _start_service(self):
        # type: (...) -> None
        # This is split in its own function to ease testing
        LOG.debug("Profiling StackCollector starting")
        self._init()
        super(StackCollector, self)._start_service()
        LOG.debug("Profiling StackCollector started")

    def _stop_service(self):
        # type: (...) -> None
        LOG.debug("Profiling StackCollector stopping")
        super(StackCollector, self)._stop_service()
        if self.tracer is not None:
            link_span = stack_v2.link_span if self._stack_collector_v2_enabled else self._thread_span_links.link_span
            self.tracer.context_provider._deregister_on_activate(link_span)
        LOG.debug("Profiling StackCollector stopped")

        # Also tell the native thread running the v2 sampler to stop, if needed
        if self._stack_collector_v2_enabled:
            stack_v2.stop()

    def _compute_new_interval(self, used_wall_time_ns):
        interval = (used_wall_time_ns / (self.max_time_usage_pct / 100.0)) - used_wall_time_ns
        return max(interval / 1e9, self.min_interval_time)

    def collect(self):
        # Compute wall time
        now = time.monotonic_ns()
        wall_time = now - self._last_wall_time
        self._last_wall_time = now
        all_events = []

        # If the stack v2 collector is enabled, then do not collect the stack samples here.
        if not self._stack_collector_v2_enabled:
            all_events = stack_collect(
                self.ignore_profiler,
                self._thread_time,
                self.nframes,
                self.interval,
                wall_time,
                self._thread_span_links,
                self.endpoint_collection_enabled,
                now_ns=now,
            )

        used_wall_time_ns = time.monotonic_ns() - now
        self.interval = self._compute_new_interval(used_wall_time_ns)

        return all_events
