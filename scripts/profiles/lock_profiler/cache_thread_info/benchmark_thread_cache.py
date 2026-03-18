#!/usr/bin/env python3
"""
Microbenchmark for per-lock thread info caching in the lock profiler.

Measures the cost of thread metadata resolution paths used in _flush_sample():
- get_ident baseline
- Old: 2 separate lookups (get_thread_name + get_thread_native_id)
- New: 1 combined lookup (get_thread_info)
- Cached: int compare hit (get_ident + get_native_id + compare, use cached)

Also simulates the realistic _flush_sample() thread-resolution portion,
multi-thread worst case (100% cache misses), and try/except vs .get() on
periodic_threads miss (to quantify exception overhead).

Run with: bash scripts/profiles/lock_profiler/cache_thread_info/run.sh
Or directly: DD_PROFILING_ENABLED=1 DD_PROFILING_LOCK_ENABLED=1 ddtrace-run python benchmark_thread_cache.py

Requires Cython-compiled _threading and _lock extensions.
"""

from __future__ import annotations

import _thread
import json
import statistics
import sys
import time


def _nanoseconds_per_call(timings: list[float]) -> float:
    """Return average ns per call from a list of per-iteration timings (in ns)."""
    return statistics.mean(timings) if timings else 0.0


def bench_get_ident(n_iters: int = 500_000) -> list[float]:
    """Baseline: _thread.get_ident() only."""
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        _ = _thread.get_ident()
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
    return timings


def bench_old_two_lookups(n_iters: int = 500_000) -> list[float]:
    """Old path: get_thread_name + get_thread_native_id (2 separate dict traversals)."""
    from ddtrace.profiling._threading import get_thread_name
    from ddtrace.profiling._threading import get_thread_native_id

    tid = _thread.get_ident()
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        _ = get_thread_name(tid)
        _ = get_thread_native_id(tid)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
    return timings


def bench_new_combined_lookup(n_iters: int = 500_000) -> list[float]:
    """New path: get_thread_info (1 combined dict traversal)."""
    from ddtrace.profiling._threading import get_thread_info

    tid = _thread.get_ident()
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        _ = get_thread_info(tid)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
    return timings


def bench_cached_hit(n_iters: int = 500_000) -> list[float]:
    """Cached hit: get_ident + get_native_id + int compare, use cached name (zero dict lookups)."""
    tid = _thread.get_ident()
    native_id = _thread.get_native_id()
    cached_tid = tid
    cached_native_id = native_id
    cached_name = "MainThread"

    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        current_tid = _thread.get_ident()
        current_native_id = _thread.get_native_id()
        if current_tid == cached_tid and current_native_id == cached_native_id:
            thread_name = cached_name
        else:
            # Should not happen in this single-threaded benchmark
            from ddtrace.profiling._threading import get_thread_info

            thread_name, _ = get_thread_info(current_tid)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = thread_name  # Prevent optimization
    return timings


def bench_flush_sample_old(n_iters: int = 500_000) -> list[float]:
    """Realistic _flush_sample simulation (old): get_ident + get_thread_name + get_thread_native_id."""
    from ddtrace.profiling._threading import get_thread_name
    from ddtrace.profiling._threading import get_thread_native_id

    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident()
        thread_name = get_thread_name(thread_id)
        thread_native_id = get_thread_native_id(thread_id)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)  # Prevent optimization
    return timings


def bench_flush_sample_cached(n_iters: int = 500_000) -> list[float]:
    """Realistic _flush_sample simulation (cached): get_ident + get_native_id + compare, use cached."""
    tid = _thread.get_ident()
    native_id = _thread.get_native_id()
    cached_tid = tid
    cached_native_id = native_id
    cached_name = "MainThread"

    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident()
        thread_native_id = _thread.get_native_id()
        if thread_id == cached_tid and thread_native_id == cached_native_id:
            thread_name = cached_name
        else:
            from ddtrace.profiling._threading import get_thread_info

            thread_name, thread_native_id = get_thread_info(thread_id)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)  # Prevent optimization
    return timings


def bench_periodic_try_except_miss(n_iters: int = 500_000) -> list[float]:
    """periodic_threads lookup on MISS: try/except KeyError pattern (current implementation)."""
    from ddtrace.internal._threads import periodic_threads
    from ddtrace.profiling._threading import get_thread_by_id

    # Use fake ID that's never in periodic_threads (or _active/_limbo) to force miss path
    fake_id = 0xDEADBEEF
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        try:
            pt = periodic_threads[fake_id]
            name = pt.name
        except KeyError:
            thread = get_thread_by_id(fake_id)
            name = thread.name if thread is not None else None
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = name  # Prevent optimization
    return timings


def bench_periodic_get_miss(n_iters: int = 500_000) -> list[float]:
    """periodic_threads lookup on MISS: .get(thread_id, None) pattern (reviewer's suggestion)."""
    from ddtrace.internal._threads import periodic_threads
    from ddtrace.profiling._threading import get_thread_by_id

    fake_id = 0xDEADBEEF
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        pt = periodic_threads.get(fake_id, None)
        if pt is not None:
            name = pt.name
        else:
            thread = get_thread_by_id(fake_id)
            name = thread.name if thread is not None else None
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = name  # Prevent optimization
    return timings


def bench_scenario1_no_changes(n_iters: int = 500_000, use_fake_thread: bool = False) -> list[float]:
    """Scenario 1: No changes. Old path: get_thread_name + get_thread_native_id (try/except)."""
    from ddtrace.profiling._threading import get_thread_name
    from ddtrace.profiling._threading import get_thread_native_id

    tid = 0xDEADBEEF if use_fake_thread else _thread.get_ident()
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident() if not use_fake_thread else tid
        thread_name = get_thread_name(thread_id)
        thread_native_id = get_thread_native_id(thread_id)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)
    return timings


def bench_scenario2_cache_only(n_iters: int = 500_000, cache_hit: bool = True) -> list[float]:
    """Scenario 2: Thread caching only. Cache uses get_thread_info (try/except) on miss."""
    from ddtrace.profiling._threading import get_thread_info

    tid = _thread.get_ident()
    native_id = _thread.get_native_id()
    fake_id = 0xDEADBEEF
    cached_tid = tid if cache_hit else -1
    cached_native_id = native_id if cache_hit else -1
    cached_name = "MainThread"
    # On cache miss, simulate different thread (use fake_id - not in periodic_threads/_active)
    miss_thread_id = fake_id if not cache_hit else tid

    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident() if cache_hit else miss_thread_id
        thread_native_id = _thread.get_native_id() if cache_hit else fake_id
        if thread_id == cached_tid and thread_native_id == cached_native_id:
            thread_name = cached_name
        else:
            thread_name, thread_native_id = get_thread_info(thread_id)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)
    return timings


def bench_scenario3_get_only(n_iters: int = 500_000, use_fake_thread: bool = False) -> list[float]:
    """Scenario 3: .get() instead of exception only. No cache. Inlined get_thread_info with .get()."""
    from ddtrace.internal._threads import periodic_threads
    from ddtrace.profiling._threading import get_thread_by_id

    fake_id = 0xDEADBEEF
    tid = fake_id if use_fake_thread else _thread.get_ident()
    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident() if not use_fake_thread else tid
        pt = periodic_threads.get(thread_id, None)
        if pt is not None:
            thread_name, thread_native_id = pt.name, thread_id
        else:
            thread = get_thread_by_id(thread_id)
            if thread is not None:
                thread_name, thread_native_id = thread.name, thread.native_id
            else:
                thread_name, thread_native_id = None, thread_id
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)
    return timings


def bench_scenario4_cache_and_get(n_iters: int = 500_000, cache_hit: bool = True) -> list[float]:
    """Scenario 4: Cache + .get(). Cache hit = no lookup. Cache miss = inlined get_thread_info with .get()."""
    from ddtrace.internal._threads import periodic_threads
    from ddtrace.profiling._threading import get_thread_by_id

    tid = _thread.get_ident()
    native_id = _thread.get_native_id()
    cached_tid = tid if cache_hit else -1
    cached_native_id = native_id if cache_hit else -1
    cached_name = "MainThread"
    fake_id = 0xDEADBEEF
    miss_thread_id = fake_id if not cache_hit else tid

    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident() if cache_hit else miss_thread_id
        thread_native_id = _thread.get_native_id() if cache_hit else fake_id
        if thread_id == cached_tid and thread_native_id == cached_native_id:
            thread_name = cached_name
        else:
            pt = periodic_threads.get(thread_id, None)
            if pt is not None:
                thread_name, thread_native_id = pt.name, thread_id
            else:
                thread = get_thread_by_id(thread_id)
                if thread is not None:
                    thread_name, thread_native_id = thread.name, thread.native_id
                else:
                    thread_name, thread_native_id = None, thread_id
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)
    return timings


def bench_cached_all_misses(n_iters: int = 500_000) -> list[float]:
    """Multi-thread worst case: cache always misses, so we call get_thread_info every time."""
    from ddtrace.profiling._threading import get_thread_info

    # Use a sentinel that never matches so we always take the get_thread_info path
    cached_tid = -1
    cached_native_id = -1

    timings = []
    for _ in range(n_iters):
        t0 = time.perf_counter_ns()
        thread_id = _thread.get_ident()
        thread_native_id = _thread.get_native_id()
        if thread_id == cached_tid and thread_native_id == cached_native_id:
            thread_name = "unused"
        else:
            thread_name, thread_native_id = get_thread_info(thread_id)
        t1 = time.perf_counter_ns()
        timings.append(t1 - t0)
        _ = (thread_id, thread_name, thread_native_id)  # Prevent optimization
    return timings


def run_matrix_benchmark(n_iters: int = 500_000, n_warmup: int = 10_000) -> dict:
    """Run 4-scenario matrix: (1) no changes, (2) cache only, (3) .get() only, (4) cache + .get()."""
    # Warmup
    for _ in range(3):
        bench_scenario1_no_changes(n_warmup, use_fake_thread=False)
        bench_scenario1_no_changes(n_warmup, use_fake_thread=True)
        bench_scenario2_cache_only(n_warmup, cache_hit=True)
        bench_scenario2_cache_only(n_warmup, cache_hit=False)
        bench_scenario3_get_only(n_warmup, use_fake_thread=False)
        bench_scenario3_get_only(n_warmup, use_fake_thread=True)
        bench_scenario4_cache_and_get(n_warmup, cache_hit=True)
        bench_scenario4_cache_and_get(n_warmup, cache_hit=False)

    matrix = {
        "1_no_changes": {
            "cache_hit": round(_nanoseconds_per_call(bench_scenario1_no_changes(n_iters, False)), 1),
            "cache_miss": round(_nanoseconds_per_call(bench_scenario1_no_changes(n_iters, True)), 1),
        },
        "2_cache_only": {
            "cache_hit": round(_nanoseconds_per_call(bench_scenario2_cache_only(n_iters, True)), 1),
            "cache_miss": round(_nanoseconds_per_call(bench_scenario2_cache_only(n_iters, False)), 1),
        },
        "3_get_only": {
            "cache_hit": round(_nanoseconds_per_call(bench_scenario3_get_only(n_iters, False)), 1),
            "cache_miss": round(_nanoseconds_per_call(bench_scenario3_get_only(n_iters, True)), 1),
        },
        "4_cache_and_get": {
            "cache_hit": round(_nanoseconds_per_call(bench_scenario4_cache_and_get(n_iters, True)), 1),
            "cache_miss": round(_nanoseconds_per_call(bench_scenario4_cache_and_get(n_iters, False)), 1),
        },
    }
    return matrix


def main() -> None:
    n_iters = 500_000
    n_warmup = 10_000

    if "--matrix" in sys.argv:
        print("4-scenario matrix benchmark (_flush_sample thread resolution)", file=sys.stderr)
        print(f"Iterations: {n_iters} (warmup: {n_warmup})", file=sys.stderr)
        print("", file=sys.stderr)
        matrix = run_matrix_benchmark(n_iters, n_warmup)
        print("", file=sys.stderr)
        print("MATRIX (avg ns per call):", file=sys.stderr)
        print("", file=sys.stderr)
        print("                         | cache hit (same thread) | cache miss (diff thread)", file=sys.stderr)
        print("  -----------------------+-------------------------+--------------------------", file=sys.stderr)
        m1, m2, m3, m4 = (
            matrix["1_no_changes"],
            matrix["2_cache_only"],
            matrix["3_get_only"],
            matrix["4_cache_and_get"],
        )
        print(f"  1. No changes          | {m1['cache_hit']:>21.1f} | {m1['cache_miss']:>24.1f}", file=sys.stderr)
        print(f"  2. Cache only          | {m2['cache_hit']:>21.1f} | {m2['cache_miss']:>24.1f}", file=sys.stderr)
        print(f"  3. .get() only         | {m3['cache_hit']:>21.1f} | {m3['cache_miss']:>24.1f}", file=sys.stderr)
        print(f"  4. Cache + .get()      | {m4['cache_hit']:>21.1f} | {m4['cache_miss']:>24.1f}", file=sys.stderr)
        print("", file=sys.stderr)
        json.dump(matrix, sys.stdout, indent=2)
        print()
        return

    print("Thread info cache microbenchmark", file=sys.stderr)
    print(f"Iterations: {n_iters} (warmup: {n_warmup})", file=sys.stderr)
    print("", file=sys.stderr)

    results = {}

    # Warmup
    for _ in range(3):
        bench_get_ident(n_warmup)
        bench_old_two_lookups(n_warmup)
        bench_new_combined_lookup(n_warmup)
        bench_cached_hit(n_warmup)
        bench_flush_sample_old(n_warmup)
        bench_flush_sample_cached(n_warmup)
        bench_cached_all_misses(n_warmup)
        bench_periodic_try_except_miss(n_warmup)
        bench_periodic_get_miss(n_warmup)

    # Thread info lookup (isolated)
    results["get_ident_baseline"] = round(_nanoseconds_per_call(bench_get_ident(n_iters)), 1)
    results["old_two_lookups"] = round(_nanoseconds_per_call(bench_old_two_lookups(n_iters)), 1)
    results["new_combined_lookup"] = round(_nanoseconds_per_call(bench_new_combined_lookup(n_iters)), 1)
    results["cached_hit"] = round(_nanoseconds_per_call(bench_cached_hit(n_iters)), 1)

    # Realistic _flush_sample simulation
    results["flush_sample_old"] = round(_nanoseconds_per_call(bench_flush_sample_old(n_iters)), 1)
    results["flush_sample_cached"] = round(_nanoseconds_per_call(bench_flush_sample_cached(n_iters)), 1)

    # Multi-thread worst case
    results["cached_all_misses"] = round(_nanoseconds_per_call(bench_cached_all_misses(n_iters)), 1)

    # try/except vs .get() on periodic_threads MISS (reviewer's suggestion)
    results["periodic_try_except_miss"] = round(_nanoseconds_per_call(bench_periodic_try_except_miss(n_iters)), 1)
    results["periodic_get_miss"] = round(_nanoseconds_per_call(bench_periodic_get_miss(n_iters)), 1)

    # Print human-readable summary
    print("=" * 60, file=sys.stderr)
    print("  Thread info lookup (isolated)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  get_ident baseline:        {results['get_ident_baseline']:>6.1f} ns", file=sys.stderr)
    print(f"  Old: 2 separate lookups:   {results['old_two_lookups']:>6.1f} ns", file=sys.stderr)
    print(f"  New: 1 combined lookup:    {results['new_combined_lookup']:>6.1f} ns", file=sys.stderr)
    print(f"  Cached: int compare hit:    {results['cached_hit']:>6.1f} ns", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Realistic _flush_sample() simulation:", file=sys.stderr)
    print(f"  Old (no cache):            {results['flush_sample_old']:>6.1f} ns", file=sys.stderr)
    print(f"  New (cached):              {results['flush_sample_cached']:>6.1f} ns", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Multi-thread worst case (100% cache misses):", file=sys.stderr)
    print(f"  Cached (all misses):        {results['cached_all_misses']:>6.1f} ns", file=sys.stderr)
    print("", file=sys.stderr)
    print("  try/except vs .get() on periodic_threads MISS:", file=sys.stderr)
    print(f"  try/except KeyError:        {results['periodic_try_except_miss']:>6.1f} ns", file=sys.stderr)
    print(f"  .get(thread_id, None):      {results['periodic_get_miss']:>6.1f} ns", file=sys.stderr)
    try_except_ns = results["periodic_try_except_miss"]
    get_ns = results["periodic_get_miss"]
    if try_except_ns > 0:
        speedup = (try_except_ns - get_ns) / try_except_ns * 100
        print(f"  → .get() is {speedup:+.1f}% (exception overhead)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    json.dump(results, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
