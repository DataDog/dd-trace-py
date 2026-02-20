import logging
import sys
import threading
import time

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector._fast_poisson import PoissonSampler


LOG = logging.getLogger(__name__)
HAS_MONITORING = hasattr(sys, "monitoring")
_current_thread = threading.current_thread


# These are global variables. We are okay with this because this is only ever accessed
# with the GIL held
cdef int _sampling_interval = 100
cdef bool _collect_message = False
cdef int _sample_counter = 0
cdef int _next_sample = 100
cdef object _sampler = None

MAX_EXCEPTION_MESSAGE_LEN = 128

cdef void _collect_exception(object exc_type, object exc_value, object exc_traceback):
    if not ddup.is_available:
        return

    cdef str module = exc_type.__module__
    cdef str exception_type = f"{module}.{exc_type.__name__}" if module else exc_type.__name__
    cdef str exception_message = ""

    cdef object handle = ddup.SampleHandle()
    handle.push_exceptioninfo(exception_type, 1)

    # Custom exception __str__ can raise with whatever exception the implementation
    # raises, so guard with fallbacks.
    if _collect_message:
        try:
            msg = str(exc_value)
            if len(msg) > MAX_EXCEPTION_MESSAGE_LEN:
                exception_message = msg[:MAX_EXCEPTION_MESSAGE_LEN] + "... (truncated)"
            else:
                exception_message = msg
        except Exception:
            exception_message = "<unprintable exception>"

        handle.push_exception_message(exception_message)

    thread = _current_thread()
    handle.push_threadinfo(thread.ident or 0, getattr(thread, "native_id", 0) or 0, thread.name)

    handle.push_pytraceback(exc_traceback)
    handle.push_monotonic_ns(time.monotonic_ns())

    handle.flush_sample()


cpdef void _on_exception_handled(object code, int instruction_offset, object exception):
    # sys.monitoring.EXCEPTION_HANDLED callback - HOT PATH
    global _sample_counter, _next_sample

    _sample_counter += 1

    if _sample_counter < _next_sample:
        return

    _next_sample = _sampler.sample(_sampling_interval) or 1
    _sample_counter = 0

    _collect_exception(type(exception), exception, exception.__traceback__)

def _on_exception_bytecode(arg: object) -> None:
    # This is the callback that is injected into the bytecode of each except block in
    # Python 3.10/3.11
    # Called at the start of each except block. Exception is in sys.exc_info().
    # arg is the (line, path, dependency_info) tuple from bytecode injection.
    global _sample_counter, _next_sample

    _sample_counter += 1

    if _sample_counter < _next_sample:
        return

    _next_sample = _fast_poisson.sample(_sampling_interval) or 1
    _sample_counter = 0

    exc_type, exc_val, exc_tb = sys.exc_info()
    if exc_type is None:
        return

    _collect_exception(exc_type, exc_val, exc_tb)


class ExceptionCollector(collector.Collector):
    # Collects exception samples using sys.monitoring (Python 3.12+)

    def __init__(self, sampling_interval: int = None, collect_message: bool = None):
        global _sampling_interval, _next_sample, _sample_counter
        global _collect_message, _sampler

        super().__init__()
        _sampling_interval = max(1, sampling_interval if sampling_interval is not None else config.exception.sampling_interval)
        _collect_message = collect_message if collect_message is not None else config.exception.collect_message

        _next_sample = _sampling_interval
        _sample_counter = 0
        _sampler = PoissonSampler()

    def _start_service(self) -> None:
        if sys.version_info >= (3, 12) and HAS_MONITORING:
            # Python 3.12+: Use sys.monitoring
            try:
                sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "dd-trace-exception-profiler")
            # Only `use_tool_id` can raise, and it will raise a `ValueError` if
            # the tool is already registered
            except ValueError:
                LOG.exception("Failed to set up exception monitoring")
                return
            sys.monitoring.set_events(sys.monitoring.PROFILER_ID, sys.monitoring.events.EXCEPTION_HANDLED)
            sys.monitoring.register_callback(
                sys.monitoring.PROFILER_ID,
                sys.monitoring.events.EXCEPTION_HANDLED,
                _on_exception_handled,
            )
            LOG.debug("Using sys.monitoring.EXCEPTION_HANDLED")
        elif sys.version_info >= (3, 10):
            # Python 3.10/3.11: Use bytecode injection
            try:
                from ddtrace.profiling.collector._exception_bytecode import install_bytecode_exception_profiling
                install_bytecode_exception_profiling(_on_exception_bytecode)
                LOG.debug("Using bytecode injection for exception profiling")
            except Exception:
                LOG.exception("Failed to set up bytecode exception profiling")
                return
        else:
            LOG.debug("Exception profiling only supports Python 3.10+, skipping")
            return

        LOG.info("ExceptionCollector started: interval=%d", _sampling_interval)

    def _stop_service(self) -> None:
        if sys.version_info >= (3, 12) and HAS_MONITORING:
            try:
                potentially_registered_tool_name = sys.monitoring.get_tool(sys.monitoring.PROFILER_ID)
                # If the tool for this id is not ours, then we shouldn't stop it
                # This will never happen because of ddtrace but it's a good guardrail
                if potentially_registered_tool_name != "dd-trace-exception-profiler":
                    LOG.debug("Tool for id %s is registered as %s, not %s; nothing to stop", sys.monitoring.PROFILER_ID, potentially_registered_tool_name, "dd-trace-exception-profiler")
                    return

                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
                sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)
            except:
                pass
        elif sys.version_info >= (3, 10):
            try:
                from ddtrace.profiling.collector._exception_bytecode import uninstall_bytecode_exception_profiling
                uninstall_bytecode_exception_profiling()
            except:
                pass
