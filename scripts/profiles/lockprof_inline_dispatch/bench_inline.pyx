# Microbenchmark only: mirrors the PR shape where the sampler.capture() check
# is inlined into acquire/__enter__/__aenter__ and the acquired_time check is
# inlined into release/__exit__/__aexit__. On the unsampled hot path these
# methods short-circuit without ever entering _acquire/_release.
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
        cdef CaptureSampler sampler = <CaptureSampler>self.capture_sampler
        if not sampler.capture():
            return self.__wrapped__.acquire(*args, **kwargs)
        return self._acquire_sampled(self.__wrapped__.acquire, *args, **kwargs)

    def _acquire_sampled(self, inner_func, *args, **kwargs):
        # Slow path — not exercised in this bench (capture_pct=0.0).
        return inner_func(*args, **kwargs)

    def release(self, *args, **kwargs):
        if self.acquired_time is None:
            return self.__wrapped__.release(*args, **kwargs)
        return self._release_sampled(self.__wrapped__.release, *args, **kwargs)

    def _release_sampled(self, inner_func, *args, **kwargs):
        # Slow path — not exercised in this bench (capture_pct=0.0).
        self.acquired_time = None
        return inner_func(*args, **kwargs)
