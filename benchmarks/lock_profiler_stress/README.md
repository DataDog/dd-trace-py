# Lock Profiler Stress Test

This benchmark provides comprehensive stress testing for the DataDog Python tracer's lock profiling functionality. It implements various scenarios designed to test the lock profiler's performance, accuracy, and robustness under different conditions.

## Overview

The lock profiler stress test includes the following scenarios:

### 1. High Contention Scenarios
- **Purpose**: Test many threads competing for few locks
- **Configurations**: 10, 50, 100 threads competing for 2 locks
- **Key Metrics**: Lock acquisition times, contention detection

### 2. Lock Hierarchy Scenarios  
- **Purpose**: Test nested lock acquisitions with proper ordering
- **Configurations**: 3, 5, 7 levels of nested locks
- **Key Metrics**: Deadlock avoidance, acquisition ordering

### 3. High Frequency Scenarios
- **Purpose**: Test rapid acquire/release cycles
- **Configurations**: 5K, 10K, 20K operations per thread
- **Key Metrics**: Profiler overhead, sampling accuracy

### 4. Mixed Synchronization Primitives
- **Purpose**: Test various Python synchronization objects
- **Includes**: Lock, RLock, Condition, Semaphore, Event
- **Key Metrics**: Cross-primitive profiling consistency

### 5. Long-Held Lock Scenarios
- **Purpose**: Test locks held for extended periods
- **Configurations**: 100ms to 500ms hold times with variance
- **Key Metrics**: Timing accuracy for long operations

### 6. Producer-Consumer Patterns
- **Purpose**: Test realistic application patterns
- **Configurations**: Multiple producers/consumers with queues
- **Key Metrics**: Queue lock profiling, throughput

### 7. Reader-Writer Patterns
- **Purpose**: Test read-heavy vs write-heavy workloads
- **Configurations**: 30+ readers, 5-10 writers
- **Key Metrics**: Read/write lock simulation profiling

### 8. Memory Pressure Scenarios
- **Purpose**: Test profiling under memory allocation stress
- **Configurations**: 1-5MB allocations per lock operation
- **Key Metrics**: GC interaction with lock profiling

### 9. Deadlock-Prone Scenarios
- **Purpose**: Test timeout handling and lock ordering
- **Configurations**: Random vs ordered lock acquisition
- **Key Metrics**: Timeout detection, error handling

### 10. Async/Await Scenarios
- **Purpose**: Test asyncio lock profiling
- **Configurations**: 100+ concurrent tasks
- **Key Metrics**: Async lock profiling accuracy

## Usage

### Running Individual Scenarios

```bash
# Run a specific scenario
cd benchmarks/lock_profiler_stress
python scenario.py --scenario_type high_contention --num_threads 50 --num_locks 2 --operations_per_thread 1000

# Run with profiling enabled
DD_PROFILING_ENABLED=1 DD_PROFILING_LOCK_ENABLED=1 python scenario.py --scenario_type mixed_primitives --num_threads 25
```

### Running All Configured Scenarios

```bash
# Run all scenarios defined in config.yaml
python ../base/run.py ./results/

# Run with profiling (to actually test the lock profiler)
DD_PROFILING_ENABLED=1 DD_PROFILING_LOCK_ENABLED=1 python ../base/run.py ./results/
```

### Running Specific Configurations

```bash
# Run only high contention tests
BENCHMARK_CONFIGS="high-contention-10-threads,high-contention-50-threads,high-contention-100-threads" python ../base/run.py ./results/
```

## Expected Output

Each scenario reports:
- **Duration**: Total execution time
- **Total operations**: Number of lock operations performed
- **Operations/sec**: Throughput metric
- **Avg/Max acquire time**: Lock acquisition timing
- **Contentions**: Number of times locks took >1ms to acquire
- **Errors**: Timeout or other error count

### Sample Output
```
Scenario: high_contention
Duration: 12.34s
Total operations: 50000
Operations/sec: 4049.45
Avg acquire time: 2.456ms
Max acquire time: 45.123ms
Contentions: 1247
Errors: 0
```

## Interpreting Results

### Performance Indicators
- **High operations/sec**: Good lock profiler performance overhead
- **Low avg acquire time**: Efficient lock implementation
- **Reasonable contentions**: Expected under high contention scenarios
- **Zero errors**: Proper timeout and error handling

### Red Flags
- **Very low operations/sec**: Possible profiler overhead issues
- **Extremely high avg acquire time**: Potential deadlocks or inefficiencies
- **High error count**: Timeout or synchronization issues
- **Inconsistent results**: Potential race conditions

## Lock Profiler Validation

When running with lock profiling enabled, you should observe:

1. **Profile Data Generation**: Check for .pprof files or profile output
2. **Lock Events**: Verify acquire/release events are captured
3. **Stack Traces**: Ensure call stacks are properly recorded
4. **Thread Information**: Verify thread IDs and names are captured
5. **Timing Accuracy**: Compare profiled times with benchmark measurements

## Customization

### Adding New Scenarios

1. Add a new method `_run_<scenario_name>` to the `LockProfilerStress` class
2. Add configuration entries to `config.yaml`
3. Document the new scenario in this README

### Modifying Existing Scenarios

Edit the parameters in `config.yaml` to adjust:
- Thread counts
- Operation counts  
- Timing parameters
- Memory allocation sizes
- Timeout values

## Common Issues

### High Error Rates
- Increase timeout values in configurations
- Reduce thread counts for deadlock-prone scenarios
- Check system resource limits

### Low Performance
- Reduce operation counts for initial testing
- Check for competing system processes
- Verify Python GIL impact on threading scenarios

### Memory Issues
- Reduce `allocate_mb_per_op` in memory pressure scenarios
- Increase available system memory
- Monitor for memory leaks in long-running tests

## Integration with CI/CD

This stress test can be integrated into continuous integration pipelines to:

1. **Regression Testing**: Detect performance regressions in lock profiling
2. **Profiler Validation**: Ensure lock profiler works across Python versions
3. **Performance Benchmarking**: Track profiler overhead over time
4. **Stress Testing**: Validate stability under extreme conditions

Example CI configuration:
```yaml
- name: Lock Profiler Stress Test
  run: |
    cd benchmarks/lock_profiler_stress
    DD_PROFILING_ENABLED=1 DD_PROFILING_LOCK_ENABLED=1 \
    python ../base/run.py ./results/
    # Validate results and check for regressions
```
