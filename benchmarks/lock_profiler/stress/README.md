# Lock Profiler Stress Tests

This benchmark suite implements comprehensive stress tests for the lock profiler. These tests are designed to ensure the lock profiler works correctly under extreme conditions and various real-world scenarios.

## Scenarios

### High Contention
Tests the profiler under conditions where many threads compete for a small number of locks.

Configurations:
- `high-contention-10-threads`: 10 threads competing for 2 locks
- `high-contention-50-threads`: 50 threads competing for 2 locks  
- `high-contention-100-threads`: 100 threads competing for 2 locks

### Lock Hierarchy
Tests nested lock acquisitions in a hierarchical manner to verify the profiler correctly tracks nested locks.

Configurations:
- `lock-hierarchy-simple`: 3 levels deep, 20 threads
- `lock-hierarchy-deep`: 5 levels deep, 30 threads
- `lock-hierarchy-complex`: 7 levels deep, 40 threads

### High Frequency
Tests rapid acquire/release cycles with minimal hold time.

Configurations:
- `high-frequency-baseline`: 20 threads, 5000 ops/thread
- `high-frequency-intense`: 30 threads, 10000 ops/thread
- `high-frequency-extreme`: 50 threads, 20000 ops/thread

### Mixed Primitives
Tests various synchronization primitives (Lock, RLock, Semaphore) together.

Configurations:
- `mixed-primitives`: 25 threads, 1000 ops/thread
- `mixed-primitives-heavy`: 50 threads, 2000 ops/thread

### Long-Held Locks
Simulates slow operations that hold locks for extended periods.

Configurations:
- `long-held-locks`: 10 threads, 100ms average hold time
- `long-held-locks-extreme`: 20 threads, 500ms average hold time

### Producer-Consumer
Tests the classic producer-consumer pattern with queues.

Configurations:
- `producer-consumer`: 10 producers, 10 consumers
- `producer-consumer-heavy`: 25 producers, 25 consumers

### Reader-Writer
Simulates reader-writer lock patterns using regular locks.

Configurations:
- `reader-writer`: 30 readers, 5 writers
- `reader-writer-heavy`: 100 readers, 10 writers

### Deadlock-Prone
Tests scenarios that could lead to deadlocks if not handled correctly with timeouts.

Configuration:
- `deadlock-prone`: Random lock acquisition order with timeouts

## Running the Benchmarks

Run a specific configuration:
```bash
scripts/perf-run-scenario lock_profiler_stress <version> "" ./artifacts/
```

Run with lock profiling enabled:
```bash
DD_PROFILING_LOCK_ENABLED=1 scripts/perf-run-scenario lock_profiler_stress <version> "" ./artifacts/
```

Compare two versions:
```bash
scripts/perf-run-scenario lock_profiler_stress ddtrace==2.8.0 . ./artifacts/
```

## What to Look For

These stress tests help identify:
1. **Memory leaks**: Does memory usage grow over time?
2. **Deadlocks**: Do any scenarios hang or timeout?
3. **Data races**: Are lock statistics accurate?
4. **Crashes**: Does the profiler crash under stress?
5. **Performance degradation**: Does throughput degrade significantly?

## Expected Behavior

All stress tests should complete without:
- Segmentation faults
- Deadlocks
- Memory exhaustion
- Data corruption
- Excessive slowdown (>2x with profiling enabled)

