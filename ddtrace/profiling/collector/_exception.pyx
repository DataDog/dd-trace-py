import logging
import sys
import threading

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _fast_poisson


LOG = logging.getLogger(__name__)
HAS_MONITORING = hasattr(sys, "monitoring")
_current_thread = threading.current_thread


cdef int _sampling_interval = 100
cdef int _sample_counter = 0
cdef int _next_sample = 100
cdef long long _total_exceptions = 0
cdef long long _sampled_exceptions = 0
cdef int _max_nframe = 64


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

    _next_sample = _fast_poisson.sample(_sampling_interval) or 1
    _sample_counter = 0

    try:
        _collect_exception(type(exception), exception, exception.__traceback__)
        _sampled_exceptions += 1
    except:
        pass


def _on_exception_bytecode(arg):
    # Bytecode injection callback for Python 3.10/3.11 - HOT PATH
    # Called at the start of each except block. Exception is in sys.exc_info().
    # arg is the (line, path, dependency_info) tuple from bytecode injection.
    global _total_exceptions, _sample_counter, _next_sample, _sampled_exceptions

    _total_exceptions += 1
    _sample_counter += 1

    if _sample_counter < _next_sample:
        return

    _next_sample = _fast_poisson.sample(_sampling_interval) or 1
    _sample_counter = 0

    exc_type, exc_val, exc_tb = sys.exc_info()
    if exc_type is None:
        return

    try:
        _collect_exception(exc_type, exc_val, exc_tb)
        _sampled_exceptions += 1
    except:
        pass


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
        if sys.version_info >= (3, 12) and HAS_MONITORING:
            # Python 3.12+: Use sys.monitoring for efficient exception tracking
            try:
                sys.monitoring.use_tool_id(sys.monitoring.PROFILER_ID, "dd-trace-exception-profiler")
                sys.monitoring.set_events(sys.monitoring.PROFILER_ID, sys.monitoring.events.EXCEPTION_HANDLED)
                sys.monitoring.register_callback(
                    sys.monitoring.PROFILER_ID,
                    sys.monitoring.events.EXCEPTION_HANDLED,
                    _on_exception_handled,
                )
                LOG.debug("Using sys.monitoring.EXCEPTION_HANDLED")
            except Exception as e:
                LOG.error("Failed to set up exception monitoring: %s", e)
                return
        elif sys.version_info >= (3, 10):
            # Python 3.10/3.11: Use bytecode injection
            try:
                from ddtrace.profiling.collector._exception_bytecode import install_bytecode_exception_profiling
                install_bytecode_exception_profiling(_on_exception_bytecode)
                LOG.debug("Using bytecode injection for exception profiling")
            except Exception as e:
                LOG.error("Failed to set up bytecode exception profiling: %s", e)
                return
        else:
            LOG.debug("Exception profiling requires Python 3.10+, skipping")
            return

        LOG.info("ExceptionCollector started: interval=%d", _sampling_interval)

    def _stop_service(self):
        if sys.version_info >= (3, 12) and HAS_MONITORING:
            try:
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

        LOG.info("ExceptionCollector stopped: total=%d, sampled=%d", _total_exceptions, _sampled_exceptions)

    @staticmethod
    def snapshot():
        pass

    def get_stats(self):
        return {"total_exceptions": _total_exceptions, "sampled_exceptions": _sampled_exceptions}
