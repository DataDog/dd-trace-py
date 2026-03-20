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

# sys.monitoring tool ID for the exception profiler.
# CPython provides IDs 0-5:
#   0 = DEBUGGER_ID
#   1 = COVERAGE_ID (used by dd-trace-py coverage)
#   2 = PROFILER_ID (used by the native stack profiler)
#   3 = used by error tracking (handled exceptions)
#   4 = **used here**
#   5 = OPTIMIZER_ID
_MONITORING_TOOL_ID = 4


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

# Reentrancy guard: _collect_exception can trigger EXCEPTION_HANDLED callbacks
# (via str(exc_value) raising, or ddup internals). Without this guard,
# the callback would recurse.
cdef bint _collecting = False


cdef void _collect_exception(_SamplerState state, object exc_type, object exc_value, object exc_traceback) except *:
    if not ddup.is_available:
        return

    cdef object handle = ddup.SampleHandle()
    handle.push_exceptioninfo(exc_type, 1)

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
    global _collecting

    if _collecting:
        return

    cdef _SamplerState state = _state
    if state is None:
        return

    state.counter += 1

    if state.counter < state.next_sample:
        return

    state.next_sample = max(state.sampler.sample(state.sampling_interval), 1)
    state.counter = 0

    # If an exception ever leaks from _collect_exception, this will silently
    # disable sys.monitoring. Guard against that and against reentrancy
    # (next_sample == 1 and _collect_exception triggers another
    # EXCEPTION_HANDLED callback internally).
    _collecting = True
    try:
        _collect_exception(state, type(exception), exception, exception.__traceback__)
    except Exception:
        LOG.debug("Failed to collect exception")
    finally:
        _collecting = False

class ExceptionCollector(collector.Collector):
    """Collects exception samples using sys.monitoring (Python 3.12+)."""

    def __init__(self, sampling_interval: int = None, collect_message: bool = None):
        super().__init__()
        raw_interval = sampling_interval if sampling_interval is not None else config.exception.sampling_interval
        assert raw_interval >= 1, "sampling_interval must be >= 1"
        self._sampling_interval = raw_interval

        self._collect_message = collect_message if collect_message is not None else config.exception.collect_message
        self._monitoring_registered = False

    def _start_service(self) -> None:
        global _state

        if _GIL_DISABLED:
            LOG.debug("Exception profiling is not supported on free-threaded CPython, skipping")
            return

        if HAS_MONITORING:
            try:
                # Claim the tool ID *before* writing _state so that a ValueError
                # (tool ID already in use) leaves the existing _state untouched.
                sys.monitoring.use_tool_id(_MONITORING_TOOL_ID, "dd-trace-exception-profiler")
                sys.monitoring.set_events(_MONITORING_TOOL_ID, sys.monitoring.events.EXCEPTION_HANDLED)
                sys.monitoring.register_callback(
                    _MONITORING_TOOL_ID,
                    sys.monitoring.events.EXCEPTION_HANDLED,
                    _on_exception_handled,
                )
            except ValueError:
                LOG.exception("Failed to set up exception monitoring")
                return

            _state = _SamplerState(self._sampling_interval, self._collect_message)
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

        # Each cleanup step is independent: always attempt all three so that
        # free_tool_id() is called even if an earlier step fails.  Failing to
        # free the tool_id permanently consumes sys.monitoring slot
        # _MONITORING_TOOL_ID and prevents any future profiler restart from
        # registering the callback again.
        try:
            sys.monitoring.register_callback(
                _MONITORING_TOOL_ID,
                sys.monitoring.events.EXCEPTION_HANDLED,
                None,
            )
        except Exception:
            LOG.debug("Failed to unregister exception monitoring callback", exc_info=True)

        try:
            sys.monitoring.set_events(_MONITORING_TOOL_ID, 0)
        except Exception:
            LOG.debug("Failed to disable exception monitoring events", exc_info=True)

        try:
            sys.monitoring.free_tool_id(_MONITORING_TOOL_ID)
        except Exception:
            LOG.debug("Failed to free exception monitoring tool_id", exc_info=True)

        self._monitoring_registered = False
        _state = None
