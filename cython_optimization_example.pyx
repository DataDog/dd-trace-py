# cython_optimization_example.pyx
#
# This is a CONCEPTUAL example showing how to optimize the lock profiling hot path
# with Cython to reduce overhead from ~650ns to ~50-100ns.
#
# This is NOT production-ready code, just an illustration of the approach.

# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True

from libc.stdint cimport int64_t
cimport cython

# Import the actual time function from C
cdef extern from "time.h":
    ctypedef long time_t
    
# Or use Python's time module (simpler but slightly slower)
from time import monotonic_ns


cdef class FastCaptureSampler:
    """Fast Cython implementation of CaptureSampler."""
    
    cdef public double capture_pct
    cdef double _counter
    
    def __init__(self, double capture_pct):
        self.capture_pct = capture_pct
        self._counter = 0.0
    
    @cython.final
    cdef inline bint should_capture(self):
        """Inlined capture check - no Python function call overhead."""
        self._counter += self.capture_pct
        if self._counter >= 100.0:
            self._counter -= 100.0
            return True
        return False


cdef class FastProfiledLock:
    """
    Optimized lock wrapper using Cython for minimal overhead.
    
    Key optimizations:
    1. C-level struct for state (no Python dict lookups)
    2. Inlined capture check (no function call)
    3. Direct delegation to wrapped lock (minimal Python overhead)
    4. __slots__ enforcement at C level (memory efficient)
    """
    
    cdef object __wrapped__           # The actual lock object
    cdef object tracer                # Optional tracer
    cdef int max_nframes              # Max frames to capture
    cdef FastCaptureSampler capture_sampler  # Sampler instance
    cdef object init_location         # Lock creation location
    cdef object acquired_time         # Last acquire timestamp (or None)
    cdef object name                  # Lock variable name
    
    def __init__(self, wrapped, tracer, int max_nframes, capture_sampler):
        self.__wrapped__ = wrapped
        self.tracer = tracer
        self.max_nframes = max_nframes
        self.capture_sampler = capture_sampler
        
        # Frame inspection (done once at init)
        import sys
        import os.path
        frame = sys._getframe(3)
        code = frame.f_code
        self.init_location = f"{os.path.basename(code.co_filename)}:{frame.f_lineno}"
        self.acquired_time = None
        self.name = None
    
    # === CRITICAL HOT PATH: acquire ===
    # This is called millions of times, so every nanosecond counts
    
    def acquire(self, *args, **kwargs):
        """Fast path for lock acquisition."""
        return self._fast_acquire(self.__wrapped__.acquire, args, kwargs)
    
    @cython.final
    cdef inline _fast_acquire(self, inner_func, tuple args, dict kwargs):
        """
        Inline acquire implementation.
        
        This avoids Python function call overhead by being cdef inline.
        The C compiler can optimize this into the caller.
        """
        # Fast path: check if we should capture (inlined, no function call)
        if not self.capture_sampler.should_capture():
            # Direct delegation - minimal overhead
            return inner_func(*args, **kwargs)
        
        # Slow path: full profiling (only for sampled operations)
        return self._slow_path_acquire(inner_func, args, kwargs)
    
    cdef _slow_path_acquire(self, inner_func, tuple args, dict kwargs):
        """Slow path with full profiling - only called for sampled operations."""
        cdef int64_t start, end
        
        start = monotonic_ns()
        try:
            result = inner_func(*args, **kwargs)
        finally:
            end = monotonic_ns()
            self.acquired_time = end
            try:
                self._update_name()
                self._flush_sample(start, end, True)  # is_acquire=True
            except:
                pass  # Never crash user code
        
        return result
    
    # === CRITICAL HOT PATH: release ===
    
    def release(self, *args, **kwargs):
        """Fast path for lock release."""
        return self._fast_release(self.__wrapped__.release, args, kwargs)
    
    @cython.final
    cdef inline _fast_release(self, inner_func, tuple args, dict kwargs):
        """
        Inline release implementation.
        
        This is the CRITICAL hot path - called on every lock release.
        """
        # Check if we have a valid acquired_time (indicates sampled acquire)
        start = self.acquired_time
        self.acquired_time = None
        
        try:
            result = inner_func(*args, **kwargs)
        finally:
            # Only profile if this was a sampled acquire
            if start is not None:
                self._flush_sample(start, monotonic_ns(), False)  # is_acquire=False
        
        return result
    
    # === Helper methods (not on hot path) ===
    
    def _update_name(self):
        """Called only for sampled operations."""
        if self.name is not None:
            return
        
        # This is expensive, but only happens once per lock (on first sample)
        import sys
        frame = sys._getframe(3)
        self.name = self._find_name(frame.f_locals) or self._find_name(frame.f_globals) or ""
    
    def _find_name(self, var_dict):
        """Find lock variable name in namespace."""
        for name, value in var_dict.items():
            if not name.startswith("__"):
                if value is self:
                    return name
        return None
    
    def _flush_sample(self, int64_t start, int64_t end, bint is_acquire):
        """Flush profiling sample to ddup."""
        from ddtrace.internal.datadog.profiling import ddup
        from ddtrace.profiling.collector import _task
        from ddtrace.profiling.collector import _traceback
        from ddtrace.profiling import _threading
        import _thread
        import sys
        
        handle = ddup.SampleHandle()
        handle.push_monotonic_ns(end)
        
        lock_name = f"{self.init_location}:{self.name}" if self.name else self.init_location
        handle.push_lock_name(lock_name)
        
        duration_ns = end - start
        if is_acquire:
            handle.push_acquire(duration_ns, 1)
        else:
            handle.push_release(duration_ns, 1)
        
        # Thread info
        thread_id = _thread.get_ident()
        thread_name = _threading.get_thread_name(thread_id)
        thread_native_id = _threading.get_thread_native_id(thread_id)
        handle.push_threadinfo(thread_id, thread_native_id, thread_name)
        
        # Task info
        task_id, task_name, task_frame = _task.get_task(thread_id)
        handle.push_task_id(task_id)
        handle.push_task_name(task_name)
        
        # Span info
        if self.tracer is not None:
            handle.push_span(self.tracer.current_span())
        
        # Stack trace
        frame = task_frame or sys._getframe(3)
        frames, _ = _traceback.pyframe_to_frames(frame, self.max_nframes)
        for ddframe in frames:
            handle.push_frame(ddframe.function_name, ddframe.file_name, 0, ddframe.lineno)
        
        handle.flush_sample()
    
    # === Delegation methods ===
    
    def __eq__(self, other):
        if isinstance(other, FastProfiledLock):
            return self.__wrapped__ == other.__wrapped__
        return self.__wrapped__ == other
    
    def __hash__(self):
        return hash(self.__wrapped__)
    
    def __repr__(self):
        return f"<FastProfiledLock({self.__wrapped__!r}) at {self.init_location}>"
    
    def locked(self):
        return self.__wrapped__.locked()
    
    def __enter__(self, *args, **kwargs):
        return self._fast_acquire(self.__wrapped__.__enter__, args, kwargs)
    
    def __exit__(self, *args, **kwargs):
        return self._fast_release(self.__wrapped__.__exit__, args, kwargs)


# === Expected Performance ===
#
# Current Python implementation:
#   - acquire() call: ~600ns (Python function call overhead)
#   - capture() call: ~50ns
#   - Total: ~650ns per operation (at 0% capture)
#
# With Cython optimization:
#   - acquire() call: ~50ns (inlined C function)
#   - capture() call: ~20ns (inlined C method)
#   - Total: ~70ns per operation (at 0% capture)
#
# Savings: ~580ns per operation (89% reduction!)
#
# For 1M lock ops/sec:
#   - Before: 650ms CPU/sec (0.65 cores)
#   - After: 70ms CPU/sec (0.07 cores)
#   - Saved: 580ms CPU/sec (0.58 cores)
#
# === Build Instructions ===
#
# 1. Add to setup.py:
#    from Cython.Build import cythonize
#    
#    ext_modules = cythonize([
#        Extension(
#            "ddtrace.profiling.collector._lock_fast",
#            ["ddtrace/profiling/collector/_lock_fast.pyx"],
#            extra_compile_args=["-O3"],
#        )
#    ])
#
# 2. Build:
#    python setup.py build_ext --inplace
#
# 3. Use in LockCollector:
#    try:
#        from ddtrace.profiling.collector._lock_fast import FastProfiledLock
#        PROFILED_LOCK_CLASS = FastProfiledLock
#    except ImportError:
#        # Fallback to pure Python
#        from ddtrace.profiling.collector._lock import _ProfiledLock
#        PROFILED_LOCK_CLASS = _ProfiledLock

