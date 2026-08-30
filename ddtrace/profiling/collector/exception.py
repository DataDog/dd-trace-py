try:
    from ddtrace.profiling.collector._exception import ExceptionCollector
except ImportError:
    # TODO(py-315): _exception is a Cython extension not compiled for all Python
    # versions (e.g. Python 3.15 before the manylinux image carries it). Define
    # a stub so profiler.py can import this module and skip the collector via
    # CollectorUnavailable rather than failing at import time.
    from ddtrace.profiling.collector import UnavailableCollector as _UnavailableCollector

    class ExceptionCollector(_UnavailableCollector):  # type: ignore[no-redef]
        pass


__all__ = ["ExceptionCollector"]
