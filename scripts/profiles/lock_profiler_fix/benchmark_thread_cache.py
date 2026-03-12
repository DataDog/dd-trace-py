"""Microbenchmark: thread info caching in lock profiler.

Compares:
  A) Old path: get_thread_name(tid) + get_thread_native_id(tid)  [two dict traversals]
  B) New path: get_thread_info(tid)                                [one dict traversal]
  C) Cached path: cache hit (just compare ints)                    [zero dict traversals]

Also benchmarks realistic _flush_sample simulation with and without caching,
and measures the memory overhead of per-lock caching.

Usage:
    python scripts/profiles/lock_profiler_fix/benchmark_thread_cache.py
"""

from __future__ import annotations

import _thread
import gc
import statistics
import sys
import threading
import time
import tracemalloc
from typing import Any
from typing import Callable
from typing import Optional


def bench(
    label: str,
    func: Callable[[], Any],
    n: int = 100_000,
    warmup: int = 5_000,
) -> tuple[float, float, float, float]:
    """Run func() n times, return (avg, median, p95, stddev) in nanoseconds."""
    for _ in range(warmup):
        func()

    times: list[float] = []
    batch: int = 1_000
    for _ in range(n // batch):
        t0: int = time.perf_counter_ns()
        for _ in range(batch):
            func()
        t1: int = time.perf_counter_ns()
        times.append((t1 - t0) / batch)

    avg: float = statistics.mean(times)
    med: float = statistics.median(times)
    p95: float = sorted(times)[int(len(times) * 0.95)]
    sd: float = statistics.stdev(times) if len(times) > 1 else 0.0
    return avg, med, p95, sd


def main() -> int:
    from ddtrace.profiling._threading import get_thread_info
    from ddtrace.profiling._threading import get_thread_name
    from ddtrace.profiling._threading import get_thread_native_id
    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    tid: int = _thread.get_ident()

    # Verify correctness
    name_old: Optional[str] = get_thread_name(tid)
    nid_old: int = get_thread_native_id(tid)
    name_new: Optional[str]
    nid_new: int
    name_new, nid_new = get_thread_info(tid)
    assert name_old == name_new, f"{name_old!r} != {name_new!r}"
    assert nid_old == nid_new, f"{nid_old!r} != {nid_new!r}"
    print(f"Thread: id={tid}, name={name_new!r}, native_id={nid_new}")
    print()

    N: int = 200_000

    # --- Benchmark A: old path (two separate lookups) ---
    def old_path() -> tuple[Optional[str], int]:
        _get_thread_name: Callable[[int], Optional[str]] = get_thread_name
        _get_thread_native_id: Callable[[int], int] = get_thread_native_id
        _tid: int = tid
        _name: Optional[str] = _get_thread_name(_tid)
        _nid: int = _get_thread_native_id(_tid)
        return _name, _nid

    avg_a: float
    med_a: float
    p95_a: float
    sd_a: float
    avg_a, med_a, p95_a, sd_a = bench("old (2 lookups)", old_path, N)

    # --- Benchmark B: new path (single combined lookup) ---
    def new_path() -> tuple[Optional[str], int]:
        _get_thread_info: Callable[[int], tuple[Optional[str], int]] = get_thread_info
        _tid: int = tid
        return _get_thread_info(_tid)

    avg_b: float
    med_b: float
    p95_b: float
    sd_b: float
    avg_b, med_b, p95_b, sd_b = bench("new (1 lookup)", new_path, N)

    # --- Benchmark C: cached path (int comparison + cached values) ---
    cached_tid: int = tid
    cached_name: Optional[str] = name_new
    cached_nid: int = nid_new

    def cached_path() -> tuple[Optional[str], int]:
        _tid: int = _thread.get_ident()
        if _tid == cached_tid:
            return cached_name, cached_nid
        return get_thread_info(_tid)

    avg_c: float
    med_c: float
    p95_c: float
    sd_c: float
    avg_c, med_c, p95_c, sd_c = bench("cached (hit)", cached_path, N)

    # --- Benchmark D: _thread.get_ident() alone (baseline) ---
    def ident_only() -> int:
        return _thread.get_ident()

    avg_d: float
    med_d: float
    p95_d: float
    sd_d: float
    avg_d, med_d, p95_d, sd_d = bench("get_ident only", ident_only, N)

    # --- Print results ---
    print(f"{'Benchmark':30s} {'avg (ns)':>10s} {'median':>10s} {'p95':>10s} {'stddev':>10s}")
    print("-" * 72)

    row: tuple[str, tuple[float, float, float, float]]
    for row in [
        ("(D) get_ident baseline", (avg_d, med_d, p95_d, sd_d)),
        ("(A) old: 2 separate lookups", (avg_a, med_a, p95_a, sd_a)),
        ("(B) new: 1 combined lookup", (avg_b, med_b, p95_b, sd_b)),
        ("(C) cached: int compare hit", (avg_c, med_c, p95_c, sd_c)),
    ]:
        label: str = row[0]
        avg: float = row[1][0]
        med: float = row[1][1]
        p95: float = row[1][2]
        sd: float = row[1][3]
        print(f"{label:30s} {avg:10.1f} {med:10.1f} {p95:10.1f} {sd:10.1f}")

    print()
    speedup_b_vs_a: float = avg_a / avg_b if avg_b > 0 else float("inf")
    speedup_c_vs_a: float = avg_a / avg_c if avg_c > 0 else float("inf")
    overhead_c: float = avg_c - avg_d
    print(f"Speedup (B vs A): {speedup_b_vs_a:.2f}x  -- combined lookup vs two separate")
    print(f"Speedup (C vs A): {speedup_c_vs_a:.2f}x  -- cached hit vs two separate")
    print(f"Cache overhead over get_ident: {overhead_c:.1f} ns  -- cost of int comparison + branch")
    print()

    # --- Benchmark E: realistic _flush_sample simulation ---
    print("=" * 72)
    print("Realistic _flush_sample simulation (N=%d)" % N)
    print("=" * 72)
    print()

    def flush_old() -> tuple[int, Optional[str], int]:
        _tid: int = _thread.get_ident()
        _name: Optional[str] = get_thread_name(_tid)
        _nid: int = get_thread_native_id(_tid)
        return _tid, _name, _nid

    _cache: dict[str, Any] = {"tid": -1, "name": None, "nid": 0}

    def flush_new_cached() -> tuple[int, Optional[str], int]:
        _tid: int = _thread.get_ident()
        if _tid == _cache["tid"]:
            return _tid, _cache["name"], _cache["nid"]
        _name: Optional[str]
        _nid: int
        _name, _nid = get_thread_info(_tid)
        _cache["tid"] = _tid
        _cache["name"] = _name
        _cache["nid"] = _nid
        return _tid, _name, _nid

    avg_e: float
    med_e: float
    p95_e: float
    sd_e: float
    avg_e, med_e, p95_e, sd_e = bench("flush old (no cache)", flush_old, N)

    avg_f: float
    med_f: float
    p95_f: float
    sd_f: float
    avg_f, med_f, p95_f, sd_f = bench("flush new (cached)", flush_new_cached, N)

    print(f"{'Benchmark':30s} {'avg (ns)':>10s} {'median':>10s} {'p95':>10s} {'stddev':>10s}")
    print("-" * 72)
    for row in [
        ("(E) flush old (no cache)", (avg_e, med_e, p95_e, sd_e)),
        ("(F) flush new (cached)", (avg_f, med_f, p95_f, sd_f)),
    ]:
        label = row[0]
        avg, med, p95, sd = row[1]
        print(f"{label:30s} {avg:10.1f} {med:10.1f} {p95:10.1f} {sd:10.1f}")

    print()
    speedup_f_vs_e: float = avg_e / avg_f if avg_f > 0 else float("inf")
    savings: float = avg_e - avg_f
    print(f"Speedup (cached vs old): {speedup_f_vs_e:.2f}x")
    print(f"Savings per _flush_sample: {savings:.1f} ns")
    print()

    # --- Benchmark G: multi-thread scenario (cache misses) ---
    print("=" * 72)
    print("Multi-thread scenario: cache miss rate impact")
    print("=" * 72)
    print()

    _cache_st: dict[str, Any] = {"tid": -1, "name": None, "nid": 0}

    def flush_cached_st() -> tuple[int, Optional[str], int]:
        _tid: int = _thread.get_ident()
        if _tid == _cache_st["tid"]:
            return _tid, _cache_st["name"], _cache_st["nid"]
        _name: Optional[str]
        _nid: int
        _name, _nid = get_thread_info(_tid)
        _cache_st["tid"] = _tid
        _cache_st["name"] = _name
        _cache_st["nid"] = _nid
        return _tid, _name, _nid

    avg_g: float
    med_g: float
    p95_g: float
    sd_g: float
    avg_g, med_g, p95_g, sd_g = bench("single-thread cached", flush_cached_st, N)

    _cache_miss: dict[str, Any] = {"tid": -1, "name": None, "nid": 0}

    def flush_cache_miss() -> tuple[int, Optional[str], int]:
        _cache_miss["tid"] = -1
        _tid: int = _thread.get_ident()
        if _tid == _cache_miss["tid"]:
            return _tid, _cache_miss["name"], _cache_miss["nid"]
        _name: Optional[str]
        _nid: int
        _name, _nid = get_thread_info(_tid)
        _cache_miss["tid"] = _tid
        _cache_miss["name"] = _name
        _cache_miss["nid"] = _nid
        return _tid, _name, _nid

    avg_h: float
    med_h: float
    p95_h: float
    sd_h: float
    avg_h, med_h, p95_h, sd_h = bench("every-call miss", flush_cache_miss, N)

    print(f"{'Benchmark':30s} {'avg (ns)':>10s} {'median':>10s} {'p95':>10s} {'stddev':>10s}")
    print("-" * 72)
    for row in [
        ("(E) old path (no cache)", (avg_e, med_e, p95_e, sd_e)),
        ("(G) cached (all hits)", (avg_g, med_g, p95_g, sd_g)),
        ("(H) cached (all misses)", (avg_h, med_h, p95_h, sd_h)),
    ]:
        label = row[0]
        avg, med, p95, sd = row[1]
        print(f"{label:30s} {avg:10.1f} {med:10.1f} {p95:10.1f} {sd:10.1f}")

    print()
    miss_speedup: float = avg_e / avg_h if avg_h > 0 else float("inf")
    print(f"Even on 100% cache misses, combined lookup speedup: {miss_speedup:.2f}x")
    print("  (because get_thread_info does 1 lookup vs 2)")
    print()

    # --- Memory cost of caching ---
    print("=" * 72)
    print("Memory cost of per-lock thread info cache")
    print("=" * 72)
    print()

    populated_tid: int = _thread.get_ident()
    populated_name: Optional[str] = threading.current_thread().name
    populated_nid: Optional[int] = threading.current_thread().native_id

    size_tid: int = sys.getsizeof(populated_tid)
    size_name: int = sys.getsizeof(populated_name)
    size_nid: int = sys.getsizeof(populated_nid)

    print("Per-slot pointer cost:             3 slots x 8 bytes = 24 bytes")
    print()
    print("Referenced object sizes (for context, NOT per-lock cost):")
    print(f"  _cached_thread_id   (int):       {size_tid} bytes  (singleton if -5..256)")
    print(f"  _cached_thread_name (str):       {size_name} bytes  (shared across locks on same thread)")
    print(f"  _cached_thread_native_id (int):  {size_nid} bytes  (singleton if -5..256)")
    print()

    # Both measurements use capture_pct=0 to avoid ddup sample buffer noise.
    # We manually populate cache fields on the second set to isolate the
    # marginal memory cost of the cached values themselves.
    num_locks: int = 10_000
    current_tid: int = _thread.get_ident()
    current_name: Optional[str] = threading.current_thread().name
    current_nid: Optional[int] = threading.current_thread().native_id

    gc.collect()
    tracemalloc.start()

    snapshot_before: tracemalloc.Snapshot = tracemalloc.take_snapshot()

    locks_without_cache: list[Any] = []
    with ThreadingLockCollector(capture_pct=0):
        for _ in range(num_locks):
            locks_without_cache.append(threading.Lock())

    snapshot_after: tracemalloc.Snapshot = tracemalloc.take_snapshot()
    stats: list[tracemalloc.StatisticDiff] = snapshot_after.compare_to(snapshot_before, "filename")
    total_alloc: int = sum(s.size_diff for s in stats if s.size_diff > 0)
    per_lock_bytes: float = total_alloc / num_locks

    print(f"Empirical measurement ({num_locks:,} locks, cache at sentinels):")
    print(f"  Total memory:     {total_alloc:>10,} bytes")
    print(f"  Per lock:         {per_lock_bytes:>10.1f} bytes")
    print()

    tracemalloc.stop()
    locks_without_cache.clear()
    gc.collect()
    tracemalloc.start()

    snapshot_before2: tracemalloc.Snapshot = tracemalloc.take_snapshot()

    locks_with_cache: list[Any] = []
    with ThreadingLockCollector(capture_pct=0):
        for _ in range(num_locks):
            lk: Any = threading.Lock()
            lk._cached_thread_id = current_tid
            lk._cached_thread_name = current_name
            lk._cached_thread_native_id = current_nid
            locks_with_cache.append(lk)

    snapshot_after2: tracemalloc.Snapshot = tracemalloc.take_snapshot()
    stats2: list[tracemalloc.StatisticDiff] = snapshot_after2.compare_to(snapshot_before2, "filename")
    total_alloc2: int = sum(s.size_diff for s in stats2 if s.size_diff > 0)
    per_lock_bytes2: float = total_alloc2 / num_locks

    print(f"Empirical measurement ({num_locks:,} locks, cache populated):")
    print(f"  Total memory:     {total_alloc2:>10,} bytes")
    print(f"  Per lock:         {per_lock_bytes2:>10.1f} bytes")
    print()

    cache_overhead: float = per_lock_bytes2 - per_lock_bytes
    print(f"Cache memory overhead per lock:  {cache_overhead:+.1f} bytes")
    print("  (slots are pre-allocated; populated cache replaces None/sentinel with shared objects)")
    print()

    tracemalloc.stop()
    locks_with_cache.clear()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
