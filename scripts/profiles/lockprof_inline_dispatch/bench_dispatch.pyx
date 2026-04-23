# Microbenchmark only: mirrors the "refactored to shared helper" shape that
# the reviewer asked about. acquire/__enter__/__aenter__ all dispatch to a
# single _acquire helper that performs the sampler.capture() check; likewise
# release/__exit__/__aexit__ dispatch to _release. Every acquire/release
# therefore pays one extra Python `def` call on the unsampled hot path.
#
# Built on the fly via pyximport when running benchmark.py.

from ddtrace.profiling.collector._sampler cimport CaptureSampler


class Lock:
    def __init__(self, wrapped, sampler):
        self.__wrapped__ = wrapped
        self.capture_sampler = sampler
        self.acquired_time = None
        self.is_internal = False

    def acquire(self, *args, **kwargs):
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)

    def _acquire(self, inner_func, *args, **kwargs):
        cdef CaptureSampler sampler = <CaptureSampler>self.capture_sampler
        if not sampler.capture():
            return inner_func(*args, **kwargs)
        # Slow path — not exercised in this bench (capture_pct=0.0).
        return inner_func(*args, **kwargs)

    def release(self, *args, **kwargs):
        return self._release(self.__wrapped__.release, *args, **kwargs)

    def _release(self, inner_func, *args, **kwargs):
        start = self.acquired_time
        self.acquired_time = None
        result = inner_func(*args, **kwargs)
        if start is None:
            return result
        # Slow path — not exercised in this bench (capture_pct=0.0).
        return result
