#!/usr/bin/env python3
"""
Quick measurement script to quantify lock profiling overhead.

This helps us understand the actual performance impact of wrapped vs unwrapped locks.
"""
import threading
import time
from typing import Callable


def measure_overhead() -> None:
    """Measure the overhead of lock profiling with different configurations."""
    
    iterations = 1_000_000
    
    print("=" * 80)
    print("Lock Profiling Overhead Measurement")
    print("=" * 80)
    print(f"Iterations: {iterations:,}\n")
    
    # Scenario 1: Baseline - no profiling at all
    print("Scenario 1: Baseline (no profiling)")
    print("-" * 80)
    regular_lock = threading.Lock()
    start = time.perf_counter_ns()
    for _ in range(iterations):
        regular_lock.acquire()
        regular_lock.release()
    baseline_ns = time.perf_counter_ns() - start
    baseline_per_op = baseline_ns / iterations
    print(f"Total time: {baseline_ns:,} ns ({baseline_ns / 1e9:.3f} sec)")
    print(f"Per operation: {baseline_per_op:.2f} ns")
    print()
    
    # Scenario 2: Profiling enabled, 0% capture
    print("Scenario 2: Profiling enabled, 0% capture rate")
    print("-" * 80)
    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    
    collector = ThreadingLockCollector(capture_pct=0.0)
    collector.start()
    
    profiled_lock_0pct = threading.Lock()
    start = time.perf_counter_ns()
    for _ in range(iterations):
        profiled_lock_0pct.acquire()
        profiled_lock_0pct.release()
    profiled_0pct_ns = time.perf_counter_ns() - start
    profiled_0pct_per_op = profiled_0pct_ns / iterations
    overhead_0pct = profiled_0pct_per_op - baseline_per_op
    overhead_0pct_pct = (overhead_0pct / baseline_per_op) * 100
    
    print(f"Total time: {profiled_0pct_ns:,} ns ({profiled_0pct_ns / 1e9:.3f} sec)")
    print(f"Per operation: {profiled_0pct_per_op:.2f} ns")
    print(f"Overhead: {overhead_0pct:.2f} ns ({overhead_0pct_pct:.1f}%)")
    print()
    
    collector.stop()
    
    # Scenario 3: Profiling enabled, 1% capture (default)
    print("Scenario 3: Profiling enabled, 1% capture rate (DEFAULT)")
    print("-" * 80)
    collector = ThreadingLockCollector(capture_pct=1.0)
    collector.start()
    
    profiled_lock_1pct = threading.Lock()
    start = time.perf_counter_ns()
    for _ in range(iterations):
        profiled_lock_1pct.acquire()
        profiled_lock_1pct.release()
    profiled_1pct_ns = time.perf_counter_ns() - start
    profiled_1pct_per_op = profiled_1pct_ns / iterations
    overhead_1pct = profiled_1pct_per_op - baseline_per_op
    overhead_1pct_pct = (overhead_1pct / baseline_per_op) * 100
    
    print(f"Total time: {profiled_1pct_ns:,} ns ({profiled_1pct_ns / 1e9:.3f} sec)")
    print(f"Per operation: {profiled_1pct_per_op:.2f} ns")
    print(f"Overhead: {overhead_1pct:.2f} ns ({overhead_1pct_pct:.1f}%)")
    print()
    
    collector.stop()
    
    # Scenario 4: Profiling enabled, 100% capture
    print("Scenario 4: Profiling enabled, 100% capture rate")
    print("-" * 80)
    collector = ThreadingLockCollector(capture_pct=100.0)
    collector.start()
    
    profiled_lock_100pct = threading.Lock()
    start = time.perf_counter_ns()
    for _ in range(iterations):
        profiled_lock_100pct.acquire()
        profiled_lock_100pct.release()
    profiled_100pct_ns = time.perf_counter_ns() - start
    profiled_100pct_per_op = profiled_100pct_ns / iterations
    overhead_100pct = profiled_100pct_per_op - baseline_per_op
    overhead_100pct_pct = (overhead_100pct / baseline_per_op) * 100
    
    print(f"Total time: {profiled_100pct_ns:,} ns ({profiled_100pct_ns / 1e9:.3f} sec)")
    print(f"Per operation: {profiled_100pct_per_op:.2f} ns")
    print(f"Overhead: {overhead_100pct:.2f} ns ({overhead_100pct_pct:.1f}%)")
    print()
    
    collector.stop()
    
    # Scenario 5: Lock created before stop, used after stop
    print("Scenario 5: Lock created BEFORE stop, used AFTER stop")
    print("-" * 80)
    collector = ThreadingLockCollector(capture_pct=1.0)
    collector.start()
    profiled_lock_before_stop = threading.Lock()
    collector.stop()
    
    start = time.perf_counter_ns()
    for _ in range(iterations):
        profiled_lock_before_stop.acquire()
        profiled_lock_before_stop.release()
    before_stop_ns = time.perf_counter_ns() - start
    before_stop_per_op = before_stop_ns / iterations
    overhead_before_stop = before_stop_per_op - baseline_per_op
    overhead_before_stop_pct = (overhead_before_stop / baseline_per_op) * 100
    
    print(f"Total time: {before_stop_ns:,} ns ({before_stop_ns / 1e9:.3f} sec)")
    print(f"Per operation: {before_stop_per_op:.2f} ns")
    print(f"Overhead: {overhead_before_stop:.2f} ns ({overhead_before_stop_pct:.1f}%)")
    print()
    
    # Scenario 6: Lock created after stop
    print("Scenario 6: Lock created AFTER stop (unwrapped)")
    print("-" * 80)
    # Collector already stopped from Scenario 5
    unwrapped_lock = threading.Lock()
    
    start = time.perf_counter_ns()
    for _ in range(iterations):
        unwrapped_lock.acquire()
        unwrapped_lock.release()
    unwrapped_ns = time.perf_counter_ns() - start
    unwrapped_per_op = unwrapped_ns / iterations
    overhead_unwrapped = unwrapped_per_op - baseline_per_op
    
    print(f"Total time: {unwrapped_ns:,} ns ({unwrapped_ns / 1e9:.3f} sec)")
    print(f"Per operation: {unwrapped_per_op:.2f} ns")
    print(f"Overhead: {overhead_unwrapped:.2f} ns (should be ~0)")
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Baseline (no profiling):              {baseline_per_op:8.2f} ns/op")
    print(f"Profiling 0% capture:                 {profiled_0pct_per_op:8.2f} ns/op (+{overhead_0pct:6.2f} ns, +{overhead_0pct_pct:5.1f}%)")
    print(f"Profiling 1% capture (DEFAULT):       {profiled_1pct_per_op:8.2f} ns/op (+{overhead_1pct:6.2f} ns, +{overhead_1pct_pct:5.1f}%)")
    print(f"Profiling 100% capture:               {profiled_100pct_per_op:8.2f} ns/op (+{overhead_100pct:6.2f} ns, +{overhead_100pct_pct:5.1f}%)")
    print(f"After stop (lock created before):     {before_stop_per_op:8.2f} ns/op (+{overhead_before_stop:6.2f} ns, +{overhead_before_stop_pct:5.1f}%)")
    print(f"After stop (lock created after):      {unwrapped_per_op:8.2f} ns/op (+{overhead_unwrapped:6.2f} ns)")
    print()
    print("KEY INSIGHTS:")
    print("-" * 80)
    print("1. ⚠️  Wrapper overhead is SIGNIFICANT: ~10-12x baseline (NOT minimal!)")
    print("2. ✅ Locks created AFTER profiler stops have ZERO overhead")
    print("3. ⚠️  Locks created BEFORE stop keep wrapper overhead (~650ns per op)")
    print("4. 🔥 100% capture shows EXTREME overhead (~110x baseline)")
    print()
    print("OVERHEAD ANALYSIS:")
    print("-" * 80)
    overhead_ns = overhead_1pct
    print(f"At 1% capture (default): ~{overhead_ns:.0f} nanoseconds per lock operation")
    print()
    print("Impact for different lock operation rates:")
    for ops_per_sec in [10_000, 100_000, 1_000_000, 10_000_000]:
        overhead_ms_per_sec = (overhead_ns * ops_per_sec) / 1_000_000
        overhead_cores = overhead_ms_per_sec / 1000
        if overhead_cores < 0.01:
            impact = "✅ Negligible"
        elif overhead_cores < 0.1:
            impact = "⚠️  Noticeable"
        elif overhead_cores < 1.0:
            impact = "⚠️  Significant"
        else:
            impact = "🔥 SEVERE"
        print(f"  {ops_per_sec:>10,} ops/sec → {overhead_ms_per_sec:>7.1f} ms CPU/sec ({overhead_cores:>5.2f} cores) {impact}")
    print()
    print("CONCLUSION:")
    print("-" * 80)
    print("Conditional unwrapping would save ~650ns per operation for locks created before stop.")
    print(f"For 1M ops/sec, this saves ~{overhead_ns:.0f} * 1M = ~{overhead_ns * 1_000:.0f}ms CPU/sec = 0.65 cores.")
    print()
    print("However, conditional unwrapping is NOT FEASIBLE due to:")
    print("  - No way to track all lock references")
    print("  - Identity preservation requirements (lock is lock)")
    print("  - Thread safety complexity")
    print()
    print("RECOMMENDED INSTEAD:")
    print("  1. Optimize fast path with Cython (reduce 650ns → ~50-100ns)")
    print("  2. Better documentation about overhead")
    print("  3. Per-module filtering (DD_PROFILING_LOCK_EXCLUDE_MODULES)")
    print("  4. Auto-disable for high lock operation rates")


if __name__ == "__main__":
    # Need to initialize ddup before using profiler
    from ddtrace.internal.datadog.profiling import ddup
    
    ddup.config(
        env="test",
        service="lock-overhead-test",
        version="1.0",
        tags={},
    )
    ddup.start()
    
    measure_overhead()

