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
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import stack_event
from ddtrace.profiling.collector import threadtime
from ddtrace.profiling.collector import traceback
from ddtrace.settings.profiling import config
from ddtrace.settings.profiling import private_config as pconfig


# These are special features that might not be available depending on your Python version and platform
FEATURES = {
    "cpu-time": threadtime.CPU_TIME,
    "stack-exceptions": sys.platform != "win32" and sys.version_info >= (3, 7),
    "transparent_events": False,
}

# These are flags indicating the enablement of the profiler.  This is handled at the level of
# a global rather than a passed parameter because this is a time of transition
use_libdd = False
use_py = True


def set_use_libdd(flag):
    global use_libdd
    use_libdd = flag


def set_use_py(flag):
    global use_py
    use_py = flag


def collect_threads(thread_id_ignore_list, thread_time, thread_span_links):
    running_threads = sys._current_frames().copy()
    current_exceptions = {_id: (exc_type, exc_tb) for _id, (exc_type, _, exc_tb) in sys._current_exceptions().items()}

    cpu_times = thread_time(running_threads.keys())

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


def stack_collect(ignore_profiler, thread_time, max_nframes, interval, wall_time, thread_span_links, collect_endpoint):
    # Do not use `threading.enumerate` to not mess with locking (gevent!)
    thread_id_ignore_list = (
        {
            thread_id
            for thread_id, thread in ddtrace_threading._active.items()
            if getattr(thread, "_ddtrace_profiling_ignore", False)
        }
        if ignore_profiler
        else set()
    )

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
                        nframes=nframes,
                        frames=frames,
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
        self, span  # type: typing.Optional[typing.Union[context.Context, ddspan.Span]]
    ):
        # type: (...) -> None
        """Link a span to its running environment.

        Track threads, tasks, etc.
        """
        # Since we're going to iterate over the set, make sure it's locked
        if isinstance(span, ddspan.Span):
            self.link_object(span)

    def get_active_span_from_thread_id(
        self, thread_id  # type: int
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


if pconfig.tb_backend == "cy":
    from ddtrace.profiling.collector._stack import StackCollector
else:

    @attr.s(slots=True)
    class StackCollector(collector.PeriodicCollector):  # type: ignore[no-redef]
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
            super(StackCollector, self)._start_service()  # type: ignore[misc]

        def _stop_service(self):
            # type: (...) -> None
            super(StackCollector, self)._stop_service()  # type: ignore[misc]
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
