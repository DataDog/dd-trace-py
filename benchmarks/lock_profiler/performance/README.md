# Lock Profiler Performance Benchmarks

This benchmark suite measures the performance overhead of the lock profiler at various sampling rates. These benchmarks help quantify the cost of enabling lock profiling in production.

## Benchmark Types

### Creation
Measures the overhead of creating lock instances.
- Baseline (0% sampling): No profiling overhead
- 1%, 10%, 100% sampling: Different profiling intensities

### Acquire/Release
Measures the overhead of lock acquire and release operations.
- Tests rapid acquire/release cycles
- Single-threaded to isolate profiler overhead

### Throughput
Measures concurrent throughput with multiple threads competing for a shared lock.
- 10 threads
- Counter increment pattern
- Tests overall system throughput

### Contention
Measures performance under high contention scenarios.
- Many threads (50) competing for few locks (5)
- Tests profiler behavior under contention

### Nested
Measures overhead of nested lock acquisitions.
- 5-level deep lock hierarchy
- Tests hierarchical tracking overhead

### Mixed Primitives
Measures overhead across different synchronization primitives.
- Regular Locks
- RLocks (reentrant locks)
- Semaphores

### Short Critical Section
Measures overhead with empty/minimal critical sections.
- Tests worst-case profiling overhead ratio
- Empty critical sections maximize overhead visibility

### Long Critical Section
Measures overhead with longer critical sections.
- 1ms sleep in critical section
- Tests best-case overhead ratio
- Real-world scenario

### Context Manager
Measures overhead when using `with` statement.
- Pythonic lock usage pattern
- Tests context manager instrumentation

### Explicit Acquire/Release
Measures overhead with explicit `acquire()` and `release()` calls.
- Manual lock management pattern
- Tests direct method call overhead

## Running the Benchmarks

Run baseline (no profiling):
```bash
scripts/perf-run-scenario lock_profiler_performance ddtrace==2.8.0 "" ./artifacts/
```

Compare with profiling enabled:
```bash
DD_PROFILING_LOCK_ENABLED=1 scripts/perf-run-scenario lock_profiler_performance ddtrace==2.8.0 . ./artifacts/
```

Run specific sampling rate:
```bash
DD_PROFILING_LOCK_ENABLED=1 DD_PROFILING_LOCK_CAPTURE_PCT=1 \
  scripts/perf-run-scenario lock_profiler_performance . "" ./artifacts/
```

## Interpreting Results

### Key Metrics

1. **Creation Overhead**: Should be < 5% at 1% sampling
2. **Acquire/Release Overhead**: Should be < 10% at 1% sampling  
3. **Throughput**: Should maintain > 95% at 1% sampling
4. **Contention**: Most sensitive to profiling overhead
5. **Nested**: Overhead should scale linearly with depth

### Acceptable Overhead

At **1% sampling** (production default):
- Creation: < 5% slowdown
- Acquire/Release: < 10% slowdown
- Throughput: > 95% of baseline
- Overall: < 5% application impact

At **100% sampling** (debugging):
- Creation: < 20% slowdown
- Acquire/Release: < 30% slowdown  
- Throughput: > 80% of baseline

### Red Flags

- Overhead > 20% at 1% sampling
- Non-linear scaling with sampling rate
- Memory leaks or unbounded growth
- Crashes or segfaults
- Deadlocks or hangs

## Comparison with Previous Implementation

The new lock profiler implementation (without wrapt) should show:
- ~50% memory reduction per lock instance
- ~10-20% faster method calls
- More predictable frame depth
- Lower overhead overall

## Example Analysis

```bash
# Run baseline and profiled versions
scripts/perf-run-scenario lock_profiler_performance baseline-commit . ./artifacts/

# Results should show:
# - Baseline: X ops/sec
# - 1% sampling: ~0.95X ops/sec (5% overhead)
# - 10% sampling: ~0.85X ops/sec (15% overhead)
# - 100% sampling: ~0.75X ops/sec (25% overhead)
```

## Profiling the Benchmarks

To profile the benchmarks themselves:
```bash
PROFILE_BENCHMARKS=1 scripts/perf-run-scenario lock_profiler_performance . "" ./artifacts/
```

Then analyze with viztracer:
```bash
vizviewer --flamegraph artifacts/<run-id>/lock_profiler_performance/<version>/viztracer/<config>.json
```

