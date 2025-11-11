# Lock Profiler Memory Benchmarks

This benchmark suite measures the memory overhead of the lock profiler implementation. It's specifically designed to compare different implementations (e.g., wrapped vs unwrapped) and detect memory leaks.

## Purpose

The memory benchmarks measure:
1. **Memory per lock instance**: How much extra memory each lock uses
2. **Memory growth**: Whether memory leaks occur over time
3. **Peak memory usage**: Maximum memory under concurrent operations
4. **Memory scaling**: How memory usage scales with sampling rate

## Scenarios

### Memory Per Lock (LockProfilerMemory)
Measures the base memory overhead of lock instances.

**Configurations:**
- `memory-small-*`: 100 locks (quick baseline)
- `memory-medium-*`: 1,000 locks (typical application)
- `memory-large-*`: 10,000 locks (heavy application)

Each with baseline (0%), 1%, 10%, and 100% sampling rates.

**What it measures:**
- Memory used per lock instance
- Total overhead for N locks
- Memory scaling with sampling rate

**Expected Results (wrapped vs unwrapped):**
- Wrapped (with wrapt): ~200-240 bytes per lock
- Unwrapped (with __slots__): ~100-120 bytes per lock
- **Expected improvement: ~50% memory reduction**

### Memory Growth (LockProfilerMemoryGrowth)
Detects memory leaks by creating and destroying locks repeatedly.

**Configurations:**
- `growth-small-*`: 50 iterations × 100 locks
- `growth-intensive-*`: 200 iterations × 200 locks

**What it measures:**
- Memory growth over time
- Whether locks are properly garbage collected
- Leak detection

**Expected Results:**
- Memory should return to baseline after gc.collect()
- No unbounded growth over iterations
- Negligible growth (< 1MB) after warmup

### Memory Under Pressure (LockProfilerMemoryPressure)
Measures memory usage under concurrent lock operations.

**Configurations:**
- `pressure-*`: 20 threads, 100 locks, 1000 ops/thread
- `pressure-heavy-*`: 50 threads, 500 locks, 2000 ops/thread

**What it measures:**
- Peak memory during concurrent operations
- Memory scaling with thread count
- Memory behavior under contention

**Expected Results:**
- Peak memory should be proportional to lock count
- No runaway memory growth during operations
- Clean cleanup after operations complete

## Running the Benchmarks

### Compare Wrapped vs Unwrapped

Compare the wrapped implementation against unwrapped:
```bash
# Run wrapped version (before optimization)
scripts/perf-run-scenario lock_profiler_memory \
  Datadog/dd-trace-py@main \
  "" \
  ./artifacts/

# Run unwrapped version (after optimization)
scripts/perf-run-scenario lock_profiler_memory \
  Datadog/dd-trace-py@vlad/lock-profiler-un-wrapted \
  "" \
  ./artifacts/
```

Direct comparison:
```bash
scripts/perf-run-scenario lock_profiler_memory \
  Datadog/dd-trace-py@main \
  Datadog/dd-trace-py@vlad/lock-profiler-un-wrapted \
  ./artifacts/
```

### Test Memory Leaks

Run growth tests with profiling enabled:
```bash
DD_PROFILING_LOCK_ENABLED=1 DD_PROFILING_LOCK_CAPTURE_PCT=100 \
  scripts/perf-run-scenario lock_profiler_memory \
  . "" ./artifacts/
```

Check the results for any configurations starting with `growth-*`.

### Measure at Different Sampling Rates

The configurations automatically test at 0%, 1%, 10%, and 100% sampling.
Compare the results to see how memory scales with sampling rate.

```bash
scripts/perf-run-scenario lock_profiler_memory . "" ./artifacts/
```

Then analyze:
- `memory-medium-baseline` (0%): No profiling overhead
- `memory-medium-1pct` (1%): Production setting
- `memory-medium-10pct` (10%): Higher resolution
- `memory-medium-100pct` (100%): Maximum overhead

## Interpreting Results

### Memory Per Lock

Look at the time measurements (which reflect memory usage):
```
memory-medium-baseline: 100ms  (0% sampling)
memory-medium-1pct:     105ms  (1% sampling)
memory-medium-10pct:    120ms  (10% sampling)
memory-medium-100pct:   150ms  (100% sampling)
```

The time increase reflects memory allocation overhead and usage.

### Expected Improvements (Wrapped → Unwrapped)

| Metric | Wrapped (wrapt) | Unwrapped (__slots__) | Improvement |
|--------|----------------|---------------------|-------------|
| Memory/lock | ~220 bytes | ~110 bytes | ~50% reduction |
| 1000 locks | ~215 KB | ~107 KB | ~50% reduction |
| 10000 locks | ~2.1 MB | ~1.0 MB | ~50% reduction |
| Peak memory | Higher | Lower | ~40-50% reduction |

### Memory Leak Detection

For `growth-*` configurations, look for:
- ✅ **Good**: Flat or decreasing time over iterations
- ❌ **Bad**: Increasing time over iterations (indicates leak)

### Red Flags

- Memory usage increases unboundedly
- Growth tests show increasing time
- Pressure tests don't release memory after completion
- Sampling rate has non-linear effect on memory

## Calculating Memory Per Lock

You can use a standalone script to get exact numbers:

```python
import tracemalloc
import threading
import gc

# Enable lock profiling if needed
# DD_PROFILING_LOCK_ENABLED=1 DD_PROFILING_LOCK_CAPTURE_PCT=100

tracemalloc.start()
gc.collect()
baseline = tracemalloc.get_traced_memory()[0]

# Create locks
num_locks = 1000
locks = [threading.Lock() for _ in range(num_locks)]

gc.collect()
current, peak = tracemalloc.get_traced_memory()
memory_used = current - baseline

print(f"Total memory for {num_locks} locks: {memory_used:,} bytes")
print(f"Memory per lock: {memory_used / num_locks:.2f} bytes")

tracemalloc.stop()
```

## Integration with Performance Benchmarks

These memory benchmarks complement the performance benchmarks:

| Benchmark Suite | Measures | Use Case |
|----------------|----------|----------|
| `lock_profiler_performance` | CPU/Time overhead | Execution speed |
| `lock_profiler_memory` | Memory overhead | Memory usage |
| `lock_profiler_stress` | Stability | Correctness |

Use all three to get a complete picture of the profiler's impact.

## Tips for Accurate Measurements

1. **Run multiple times**: Memory measurements can vary
2. **Check baseline**: Always compare against 0% sampling
3. **Use large scales**: Small lock counts have noise
4. **Monitor growth**: Run growth tests for extended periods
5. **Profile memory**: Use memory_profiler or heapy for detailed analysis
6. **Compare implementations**: Run same tests on different branches

## Example Workflow

To measure a new optimization:

```bash
# 1. Run baseline (before optimization)
scripts/perf-run-scenario lock_profiler_memory main "" ./artifacts/baseline/

# 2. Run optimized version
scripts/perf-run-scenario lock_profiler_memory feature-branch "" ./artifacts/optimized/

# 3. Compare results
diff -u artifacts/baseline/results.json artifacts/optimized/results.json

# 4. Look for:
#    - Lower memory usage (better)
#    - No growth over time (good)
#    - Consistent scaling (expected)
```

## Debugging High Memory Usage

If memory usage is higher than expected:

1. Check for reference cycles
2. Verify __slots__ is used (not __dict__)
3. Look for cached data structures
4. Profile with memory_profiler
5. Check for global state retention

Example profiling:
```python
from memory_profiler import profile

@profile
def test_lock_creation():
    locks = [threading.Lock() for _ in range(1000)]
    return locks
```

## Related Documentation

- `LOCK_PROFILER_BENCHMARKS.md`: Main benchmark guide
- `benchmarks/lock_profiler_performance/README.md`: CPU benchmarks
- `benchmarks/lock_profiler_stress/README.md`: Stress tests

