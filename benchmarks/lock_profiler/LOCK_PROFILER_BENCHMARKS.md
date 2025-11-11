# Lock Profiler Benchmarks Guide

This document describes the benchmark/stress test experiments created for the lock profiler on the `vlad/lock-profiler-doe` branch.

## Overview

Three comprehensive benchmark suites have been created:

1. **Lock Profiler Stress Tests** (`benchmarks/lock_profiler_stress/`)
   - Tests the profiler under extreme conditions
   - Various real-world scenarios (contention, hierarchies, producer-consumer, etc.)
   - Verifies correctness and stability

2. **Lock Profiler Performance** (`benchmarks/lock_profiler_performance/`)
   - Measures CPU/time overhead at different sampling rates
   - Quantifies the execution speed impact of enabling lock profiling
   - Compares baseline vs profiled performance

3. **Lock Profiler Memory** (`benchmarks/lock_profiler_memory/`)
   - Measures memory overhead per lock instance
   - Detects memory leaks over time
   - Compares wrapped vs unwrapped implementations
   - **Key for measuring optimization impact!**

## Quick Start

### Comparing Wrapped vs Unwrapped (Your Use Case!)

To measure the impact of the unwrapping optimization:

```bash
# 1. Test CPU performance comparison
scripts/perf-run-scenario lock_profiler_performance \
  Datadog/dd-trace-py@main \
  Datadog/dd-trace-py@vlad/lock-profiler-un-wrapted \
  ./artifacts/cpu_comparison/

# 2. Test MEMORY comparison (most important!)
scripts/perf-run-scenario lock_profiler_memory \
  Datadog/dd-trace-py@main \
  Datadog/dd-trace-py@vlad/lock-profiler-un-wrapted \
  ./artifacts/memory_comparison/

# 3. Test stability
scripts/perf-run-scenario lock_profiler_stress \
  Datadog/dd-trace-py@vlad/lock-profiler-un-wrapted \
  "" \
  ./artifacts/stress_test/
```

**Expected Results:**
- **Memory**: ~50% reduction per lock (220 bytes → 110 bytes)
- **CPU**: ~10-20% faster method calls
- **Stress**: All tests pass without issues

### Running Stress Tests

Test a specific stress scenario:
```bash
scripts/perf-run-scenario lock_profiler_stress . "" ./artifacts/
```

Test with lock profiling enabled:
```bash
DD_PROFILING_LOCK_ENABLED=1 scripts/perf-run-scenario lock_profiler_stress . "" ./artifacts/
```

### Running Performance Benchmarks

Baseline (no profiling):
```bash
scripts/perf-run-scenario lock_profiler_performance . "" ./artifacts/
```

With 1% sampling (production default):
```bash
DD_PROFILING_LOCK_ENABLED=1 DD_PROFILING_LOCK_CAPTURE_PCT=1 \
  scripts/perf-run-scenario lock_profiler_performance . "" ./artifacts/
```

With 100% sampling (maximum overhead):
```bash
DD_PROFILING_LOCK_ENABLED=1 DD_PROFILING_LOCK_CAPTURE_PCT=100 \
  scripts/perf-run-scenario lock_profiler_performance . "" ./artifacts/
```

### Running Memory Benchmarks

Test memory overhead at different scales:
```bash
scripts/perf-run-scenario lock_profiler_memory . "" ./artifacts/
```

This tests memory at 0%, 1%, 10%, and 100% sampling rates across different scales.

## Stress Test Scenarios

### High Contention
Many threads competing for few locks.
- **Configs**: `high-contention-10-threads`, `high-contention-50-threads`, `high-contention-100-threads`
- **Purpose**: Test profiler under lock contention
- **What to verify**: No deadlocks, correct stats

### Lock Hierarchy
Nested lock acquisitions.
- **Configs**: `lock-hierarchy-simple`, `lock-hierarchy-deep`, `lock-hierarchy-complex`
- **Purpose**: Verify nested lock tracking
- **What to verify**: Correct nesting depth tracking

### High Frequency
Rapid acquire/release cycles.
- **Configs**: `high-frequency-baseline`, `high-frequency-intense`, `high-frequency-extreme`
- **Purpose**: Test profiler with high operation rate
- **What to verify**: No memory leaks, stable performance

### Mixed Primitives
Various synchronization primitives (Lock, RLock, Semaphore).
- **Configs**: `mixed-primitives`, `mixed-primitives-heavy`
- **Purpose**: Test profiler with different primitive types
- **What to verify**: All primitives tracked correctly

### Long-Held Locks
Simulates slow operations holding locks.
- **Configs**: `long-held-locks`, `long-held-locks-extreme`
- **Purpose**: Test profiler with long critical sections
- **What to verify**: Accurate timing measurements

### Producer-Consumer
Classic producer-consumer pattern with queues.
- **Configs**: `producer-consumer`, `producer-consumer-heavy`
- **Purpose**: Test profiler in realistic workload
- **What to verify**: Queue operations tracked

### Reader-Writer
Reader-writer lock patterns.
- **Configs**: `reader-writer`, `reader-writer-heavy`
- **Purpose**: Test profiler with read/write patterns
- **What to verify**: Correct read/write distinction

### Deadlock-Prone
Random lock acquisition with timeouts.
- **Config**: `deadlock-prone`
- **Purpose**: Test profiler resilience to timeout scenarios
- **What to verify**: No actual deadlocks, graceful timeout handling

## Performance Benchmark Types

### Creation
Measures lock creation overhead.
- **Configs**: `creation-baseline`, `creation-1pct`, `creation-10pct`, `creation-100pct`
- **Expected**: < 5% overhead at 1% sampling

### Acquire/Release
Measures lock operation overhead.
- **Configs**: `acquire-release-baseline`, `acquire-release-1pct`, `acquire-release-10pct`, `acquire-release-100pct`
- **Expected**: < 10% overhead at 1% sampling

### Throughput
Measures concurrent throughput.
- **Configs**: `throughput-baseline`, `throughput-1pct`, `throughput-10pct`, `throughput-100pct`
- **Expected**: > 95% of baseline at 1% sampling

### Contention
Performance under high contention.
- **Configs**: `contention-baseline`, `contention-1pct`, `contention-10pct`, `contention-100pct`
- **Expected**: Most sensitive to profiling overhead

### Nested
Nested lock acquisitions.
- **Configs**: `nested-baseline`, `nested-1pct`, `nested-10pct`, `nested-100pct`
- **Expected**: Linear scaling with depth

### Mixed Primitives
Different synchronization primitives.
- **Configs**: `mixed-primitives-baseline`, `mixed-primitives-1pct`, etc.
- **Expected**: Consistent overhead across primitive types

### Short/Long Critical Sections
- **Short configs**: `short-critical-baseline`, `short-critical-1pct`, etc.
- **Long configs**: `long-critical-baseline`, `long-critical-1pct`, etc.
- **Expected**: Lower relative overhead with longer critical sections

### Context Manager vs Explicit
- **Context configs**: `context-manager-baseline`, `context-manager-1pct`, etc.
- **Explicit configs**: `explicit-baseline`, `explicit-1pct`, etc.
- **Expected**: Similar overhead for both patterns

## Memory Benchmarks (New!)

The memory benchmarks are specifically designed to measure the overhead of different lock profiler implementations.

### Scenarios

1. **Memory Per Lock** (`LockProfilerMemory`)
   - Small (100 locks), Medium (1,000 locks), Large (10,000 locks)
   - Measures base memory footprint
   - **Key metric for wrapped vs unwrapped comparison**

2. **Memory Growth** (`LockProfilerMemoryGrowth`)
   - Creates and destroys locks repeatedly
   - Detects memory leaks
   - Verifies proper garbage collection

3. **Memory Pressure** (`LockProfilerMemoryPressure`)
   - Concurrent lock operations
   - Peak memory measurement
   - Scaling under load

### Expected Results: Wrapped vs Unwrapped

| Implementation | Memory/Lock | 1000 Locks | 10000 Locks | Improvement |
|---------------|-------------|------------|-------------|-------------|
| **Wrapped** (wrapt) | ~220 bytes | ~215 KB | ~2.1 MB | Baseline |
| **Unwrapped** (__slots__) | ~110 bytes | ~107 KB | ~1.0 MB | **~50% reduction** |

### Why This Matters

1. **Quantifiable Impact**: Shows exact memory savings from optimizations
2. **Production Impact**: Large apps with thousands of locks will save MBs of memory
3. **Regression Detection**: Future changes won't increase memory usage
4. **Optimization Validation**: Proves optimizations work as expected

## Comparing Versions

### Compare CPU Performance

Compare current branch against main:
```bash
scripts/perf-run-scenario lock_profiler_performance Datadog/dd-trace-py@main . ./artifacts/
```

Compare against a specific commit:
```bash
scripts/perf-run-scenario lock_profiler_performance <commit-hash> . ./artifacts/
```

Compare two branches:
```bash
scripts/perf-run-scenario lock_profiler_performance \
  Datadog/dd-trace-py@vlad/benchmark-lock-profiler \
  Datadog/dd-trace-py@vlad/lock-profiler-doe \
  ./artifacts/
```

### Compare Memory Usage

**This is the key comparison for wrapped vs unwrapped!**

```bash
# Direct comparison of memory overhead
scripts/perf-run-scenario lock_profiler_memory \
  Datadog/dd-trace-py@main \
  Datadog/dd-trace-py@vlad/lock-profiler-un-wrapted \
  ./artifacts/memory/

# Look for results in:
# - memory-medium-100pct: Best shows the difference
# - memory-large-100pct: Shows impact at scale
```

### Future Optimizations

To measure any future optimization:

```bash
# 1. Establish baseline (current implementation)
scripts/perf-run-scenario lock_profiler_memory main "" ./artifacts/baseline/
scripts/perf-run-scenario lock_profiler_performance main "" ./artifacts/baseline/

# 2. Test optimized implementation
scripts/perf-run-scenario lock_profiler_memory your-branch "" ./artifacts/optimized/
scripts/perf-run-scenario lock_profiler_performance your-branch "" ./artifacts/optimized/

# 3. Compare results
# Memory improvements will show in timing differences
# CPU improvements will show in execution speed
```

## Profiling the Benchmarks

Generate profiling data using viztracer:
```bash
PROFILE_BENCHMARKS=1 scripts/perf-run-scenario lock_profiler_stress . "" ./artifacts/
```

View results:
```bash
# Install viztracer
pip install -U viztracer

# View a specific scenario
vizviewer artifacts/<run-id>/lock_profiler_stress/<version>/viztracer/<config_name>.json

# View as flamegraph
vizviewer --flamegraph artifacts/<run-id>/lock_profiler_stress/<version>/viztracer/<config_name>.json
```

## Interpreting Results

### Stress Tests
- **Success**: All scenarios complete without crashes or hangs
- **Memory**: No unbounded memory growth
- **Timing**: Execution time should be reasonable (< 2x slowdown with profiling)
- **Errors**: Check logs for any exceptions or warnings

### Performance Benchmarks
- **Baseline**: Establishes performance without profiling
- **1% sampling**: Production default, should show minimal overhead (< 5%)
- **10% sampling**: Higher resolution, acceptable overhead (< 15%)
- **100% sampling**: Full profiling, higher overhead acceptable (< 30%)

### Red Flags
- Overhead > 20% at 1% sampling
- Non-linear scaling with sampling rate
- Memory leaks (growing memory usage)
- Crashes or segfaults
- Deadlocks or hangs
- Significant variance between runs

## Continuous Integration

These benchmarks can be integrated into CI to:
1. Detect performance regressions
2. Verify stability across platforms
3. Track overhead trends over time
4. Validate new profiler features

Example CI usage:
```yaml
benchmark-lock-profiler:
  script:
    - scripts/perf-run-scenario lock_profiler_performance baseline-commit . ./artifacts/
    - python scripts/analyze_benchmark_results.py artifacts/
```

## Tips and Best Practices

1. **Run multiple times**: Benchmarks can have variance, run at least 3 times
2. **Warm up**: First run might be slower due to JIT, code loading, etc.
3. **Isolate**: Run on a quiet system without other heavy processes
4. **Document**: Note system specs, Python version, OS when comparing
5. **Baseline**: Always compare against a baseline (0% sampling)
6. **Incremental**: Test different sampling rates (0%, 1%, 10%, 100%)
7. **Profile**: Use PROFILE_BENCHMARKS=1 to identify hotspots

## Environment Variables

### Lock Profiling
- `DD_PROFILING_LOCK_ENABLED`: Enable/disable lock profiling (default: false)
- `DD_PROFILING_LOCK_CAPTURE_PCT`: Sampling percentage 0-100 (default: 1)

### Benchmarking
- `PROFILE_BENCHMARKS`: Generate viztracer profiles (default: 0)

## Troubleshooting

### Benchmark fails to run
- Check that all dependencies are installed
- Verify Docker is running (if using containerized benchmarks)
- Check Python version compatibility

### Results seem wrong
- Ensure system is not under load
- Check for background processes affecting timing
- Verify correct environment variables are set
- Run multiple times and check variance

### Out of memory
- Reduce operations_per_thread in config
- Reduce num_threads in config
- Use a machine with more memory
- Check for memory leaks in the code

## Related Files

- `benchmarks/lock_profiler_stress/scenario.py`: Stress test implementation
- `benchmarks/lock_profiler_stress/config.yaml`: Stress test configurations
- `benchmarks/lock_profiler_performance/scenario.py`: Performance benchmark implementation
- `benchmarks/lock_profiler_performance/config.yaml`: Performance configurations
- `benchmarks/README.rst`: General benchmark framework documentation
- `scripts/perf-run-scenario`: Script to run benchmarks

## References

Based on work from:
- `vlad/benchmark-lock-profiler` branch: Performance benchmarking framework
- `vlad/lock_profiler_stress_test` branch: Stress test scenarios
- DOE (Design of Experiments) benchmark framework

## Support

For issues or questions:
1. Check the README files in each benchmark directory
2. Review the main benchmarks/README.rst
3. Consult the team or file an issue

