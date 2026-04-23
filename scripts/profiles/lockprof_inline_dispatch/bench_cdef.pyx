# Microbenchmark only: shows what the reviewer's "the compiler would inline
# it" hypothesis actually buys. Lock is a `cdef class` here, so the helper
# `_should_sample` can be a true `cdef inline bint` with C-level dispatch —
# the compiler literally inlines the call. This is the shape a larger
# refactor would require (cdef class constraints: single inheritance, fixed
# layout, no dynamic attributes), which conflicts with how _ProfiledLock
# currently exposes `acquired_time`, `__wrapped__`, etc. to Python callers.
#
# Included purely as context for "how much does a cdef inline actually save
# vs. a Python-level def helper?" — not as a proposed implementation.
#
# Built on the fly via pyximport when running benchmark.py.

from ddtrace.profiling.collector._sampler cimport CaptureSampler


cdef class Lock:
    cdef public object __wrapped__
    cdef CaptureSampler capture_sampler
    cdef public object acquired_time
    cdef public bint is_internal

    def __init__(self, wrapped, CaptureSampler sampler):
        self.__wrapped__ = wrapped
        self.capture_sampler = sampler
        self.acquired_time = None
        self.is_internal = False

    cdef inline bint _should_sample(self):
        return self.capture_sampler.capture()

    def acquire(self, *args, **kwargs):
        if not self._should_sample():
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
