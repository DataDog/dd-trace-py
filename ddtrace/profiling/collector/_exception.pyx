import logging
import sysconfig as _sysconfig
import sys
import threading
import time

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector._fast_poisson import PoissonSampler


LOG = logging.getLogger(__name__)
HAS_MONITORING = hasattr(sys, "monitoring")
_GIL_DISABLED = _sysconfig.get_config_var("Py_GIL_DISABLED")
_current_thread = threading.current_thread

MAX_EXCEPTION_MESSAGE_LEN = 128


cdef class _SamplerState:
    """
    Accessed via the single module-level ``_state`` global from the
    sys.monitoring callback

    Free-threaded CPython is guarded in
    ExceptionCollector._start_service; if free-threading support is
    added, these fields need synchronization
    """
    cdef int sampling_interval
    cdef bint collect_message
    cdef int counter
    cdef int next_sample
    cdef object sampler  # PoissonSampler

    def __init__(self, int sampling_interval, bint collect_message):
        self.sampling_interval = sampling_interval
        self.collect_message = collect_message
        self.counter = 0
        self.next_sample = sampling_interval
        self.sampler = PoissonSampler()


# Single module-level global: None when inactive, set by ExceptionCollector. This
# is okay to be global. Data race is protected by the GIL and we actually want threads
# to share sampler state, because the profiler is process scoped, not thread scoped
cdef _SamplerState _state = None


cdef void _collect_exception(_SamplerState state, object exc_type, object exc_value, object exc_traceback) except *:
    if not ddup.is_available:
        return

    cdef str module = exc_type.__module__
    cdef str exception_type = f"{module}.{exc_type.__name__}" if module else exc_type.__name__

    cdef object handle = ddup.SampleHandle()
    handle.push_exceptioninfo(exception_type, 1)

    # Custom exception __str__ can raise, so guard with fallbacks.
    if state.collect_message:
        try:
            msg = str(exc_value)
            if len(msg) > MAX_EXCEPTION_MESSAGE_LEN:
                exception_message = msg[:MAX_EXCEPTION_MESSAGE_LEN] + "... (truncated)"
            else:
                exception_message = msg
        # We don't know the internal implementation of a potential custom exception
        # raise, so we have to catch all exception types
        except Exception:
            exception_message = "<unprintable exception>"

        handle.push_exception_message(exception_message)

    thread = _current_thread()
    handle.push_threadinfo(thread.ident or 0, getattr(thread, "native_id", 0) or 0, thread.name)

    handle.push_pytraceback(exc_traceback)
    handle.push_monotonic_ns(time.monotonic_ns())

    handle.flush_sample()


cpdef void _on_exception_handled(object code, int instruction_offset, object exception):
    """sys.monitoring.EXCEPTION_HANDLED callback — HOT PATH."""
    cdef bint _collecting = False
    if _collecting:
        return

    _collecting = True
    cdef _SamplerState state = _state
    if state is None:
        return

    state.counter += 1

    if state.counter < state.next_sample:
        return

    state.next_sample = max(state.sampler.sample(state.sampling_interval), 1)
    state.counter = 0

    # If an exception ever leaks from _collect_exception, this will silently disable
    # sys.monitoring. This is rare, but we should catch all exception here to avoid this
    #
    # Rare, but if next_sample is 1, then reentrant calls will cause this to fire again
    # We should guard against re-entrancy explictly here
    try:
        _collect_exception(state, type(exception), exception, exception.__traceback__)
    except Exception:
        LOG.exception("Failed to collect exception")
    finally:
        _collecting = False

class ExceptionCollector(collector.Collector):
    """Collects exception samples using sys.monitoring (Python 3.12+)."""

    def __init__(self, sampling_interval: int = None, collect_message: bool = None):
        super().__init__()

        raw_interval = sampling_interval if sampling_interval is not None else config.exception.sampling_interval
        self._sampling_interval = raw_interval if raw_interval >= 1 else 100
        self._collect_message = collect_message if collect_message is not None else config.exception.collect_message
        self._monitoring_registered = False

    def _start_service(self) -> None:
        global _state

        if _GIL_DISABLED:
            LOG.debug("Exception profiling is not supported on free-threaded CPython, skipping")
            return

        if HAS_MONITORING:
            try:
                _state = _SamplerState(self._sampling_interval, self._collect_message)
                sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "dd-trace-exception-profiler")
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, sys.monitoring.events.EXCEPTION_HANDLED)
                sys.monitoring.register_callback(
                    sys.monitoring.PROFILER_ID,
                    sys.monitoring.events.EXCEPTION_HANDLED,
                    _on_exception_handled,
                )
            except ValueError:
                LOG.exception("Failed to set up exception monitoring")
                return

            self._monitoring_registered = True
        else:
            LOG.debug("Exception profiling only supports Python 3.12+, skipping")
            return

        LOG.debug("ExceptionCollector started: interval=%d", _state.sampling_interval)

    def _stop_service(self) -> None:
        global _state

        if not self._monitoring_registered:
            _state = None
            return

        try:
            sys.monitoring.register_callback(
                sys.monitoring.PROFILER_ID,
                sys.monitoring.events.EXCEPTION_HANDLED,
                None,
            )
            sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
            sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)
        except Exception:
            LOG.debug("Failed to clean up exception monitoring", exc_info=True)
        finally:
            self._monitoring_registered = False
            _state = None
