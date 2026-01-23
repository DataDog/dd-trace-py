"""
Cython implementation of exception profiling collector.
Uses sys.monitoring (Python 3.12+) for efficient exception tracking.
"""

import logging
import sys
import threading

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector

from libc.stdlib cimport rand, RAND_MAX
from libc.math cimport log


LOG = logging.getLogger(__name__)
HAS_MONITORING = hasattr(sys, "monitoring")
_current_thread = threading.current_thread


cdef int _sampling_interval = 100
cdef int _sample_counter = 0
cdef int _next_sample = 100
cdef long long _total_exceptions = 0
cdef long long _sampled_exceptions = 0
cdef int _max_nframe = 64
cdef object _original_excepthook = None


cdef inline double _random_double() noexcept nogil:
    return (<double>rand() + 1.0) / (<double>RAND_MAX + 2.0)


cdef inline int _poisson_next(int mean) noexcept nogil:
    cdef double u, result
    with nogil:
        u = _random_double()
        result = -(<double>mean) * log(u)
    return <int>result if result >= 1.0 else 1


cdef void _collect_exception(object exc_type, object exc_value, object exc_traceback):
    if not ddup.is_available:
        return

    cdef str module = exc_type.__module__
    cdef str exception_type = f"{module}.{exc_type.__name__}" if module else exc_type.__name__
    cdef object handle = ddup.SampleHandle()
    cdef object tb = exc_traceback
    cdef list frames = []
    cdef int frame_count = 0
    cdef object code
    cdef int i
    cdef str func
    cdef str filename
    cdef int lineno

    try:
        handle.push_exceptioninfo(exception_type, 1)

        thread = _current_thread()
        handle.push_threadinfo(thread.ident or 0, getattr(thread, "native_id", 0) or 0, thread.name)

        while tb is not None and frame_count < _max_nframe:
            code = tb.tb_frame.f_code
            frames.append((code.co_name, code.co_filename, tb.tb_lineno))
            frame_count += 1
            tb = tb.tb_next

        for i in range(len(frames) - 1, -1, -1):
            func, filename, lineno = frames[i]
            handle.push_frame(func, filename, 0, lineno)

        handle.flush_sample()
    except:
        handle.drop_sample()
        raise


cpdef void _on_exception_handled(object code, int instruction_offset, object exception):
    # sys.monitoring.EXCEPTION_HANDLED callback - HOT PATH
    global _total_exceptions, _sample_counter, _next_sample, _sampled_exceptions

    _total_exceptions += 1
    _sample_counter += 1

    if _sample_counter < _next_sample:
        return

    _next_sample = _poisson_next(_sampling_interval)
    _sample_counter = 0

    try:
        _collect_exception(type(exception), exception, exception.__traceback__)
        _sampled_exceptions += 1
    except:
        pass


cpdef void _excepthook(object exc_type, object exc_value, object exc_traceback):
    # sys.excepthook handler for uncaught exceptions
    global _total_exceptions, _sample_counter, _next_sample, _sampled_exceptions

    _total_exceptions += 1
    _sample_counter += 1

    if _sample_counter >= _next_sample:
        _next_sample = _poisson_next(_sampling_interval)
        _sample_counter = 0
        try:
            _collect_exception(exc_type, exc_value, exc_traceback)
            _sampled_exceptions += 1
        except:
            pass

    if _original_excepthook is not None:
        _original_excepthook(exc_type, exc_value, exc_traceback)


class ExceptionCollector(collector.Collector):
    # Collects exception samples using sys.monitoring (Python 3.12+)

    def __init__(self, max_nframe=None, sampling_interval=None, collect_message=None):
        global _sampling_interval, _max_nframe, _next_sample, _sample_counter
        global _total_exceptions, _sampled_exceptions

        super().__init__()
        _max_nframe = max_nframe if max_nframe is not None else config.max_frames
        _sampling_interval = sampling_interval if sampling_interval is not None else getattr(config.exception, "sampling_interval", 100)
        self.collect_message = collect_message if collect_message is not None else getattr(config.exception, "collect_message", True)

        _next_sample = _sampling_interval
        _sample_counter = 0
        _total_exceptions = 0
        _sampled_exceptions = 0

    def _start_service(self):
        global _original_excepthook

        if HAS_MONITORING and sys.version_info >= (3, 12):
            try:
                sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "dd-trace-exception-profiler")
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, sys.monitoring.events.EXCEPTION_HANDLED)
                sys.monitoring.register_callback(
                    sys.monitoring.PROFILER_ID,
                    sys.monitoring.events.EXCEPTION_HANDLED,
                    _on_exception_handled,
                )
                _original_excepthook = sys.excepthook
                sys.excepthook = _excepthook
                LOG.debug("Using sys.monitoring.EXCEPTION_HANDLED")
            except Exception as e:
                LOG.debug("Failed to set up monitoring: %s", e)
                _original_excepthook = sys.excepthook
                sys.excepthook = _excepthook
        else:
            _original_excepthook = sys.excepthook
            sys.excepthook = _excepthook
            LOG.debug("Using sys.excepthook only")

        LOG.info("ExceptionCollector started: interval=%d", _sampling_interval)

    def _stop_service(self):
        global _original_excepthook

        if HAS_MONITORING and sys.version_info >= (3, 12):
            try:
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, 0)
                sys.monitoring.free_tool_id(sys.monitoring.PROFILER_ID)
            except:
                pass

        if _original_excepthook is not None:
            sys.excepthook = _original_excepthook
            _original_excepthook = None

        LOG.info("ExceptionCollector stopped: total=%d, sampled=%d", _total_exceptions, _sampled_exceptions)

    def snapshot(self):
        pass

    def get_stats(self):
        return {"total_exceptions": _total_exceptions, "sampled_exceptions": _sampled_exceptions}
