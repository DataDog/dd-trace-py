"""Microbenchmark for the PyCodeObject* -> function_id cache.

Targets the heap profiler ADD side (sample construction at allocation time),
not the export path. The cache short-circuits ProfilesDictionary::insert_str x2
and insert_function x1 per frame on cache hits.

A/B mode (default): runs each scenario twice, once with the cache enabled and
once with it disabled (via _memalloc.code_cache_disable()), then reports the
speedup ratio.

Scenarios vary active-set size relative to the cache capacity. Default
cap is 1024 ways (512 sets x 2) which fits comfortably in L1 D-cache:

  * tiny (10 unique frames)    - well under cap, ~100% hits after warmup
  * small (1k unique frames)   - right at cap, mostly hits
  * mid (16k unique frames)    - 16x over cap, exercises FIFO eviction
  * full (32k unique frames)   - 32x over cap
  * churn (64k unique frames)  - 64x over cap, heavy eviction churn

Hot loop: each scenario calls N unique helper functions in round-robin so
each call has its own PyCodeObject and the allocator hook samples that
function's frame on every alloc.

Run:
    python scripts/code_cache_microbench.py

To override cache capacity:
    DD_PROFILING_MEMALLOC_CODE_CACHE_SIZE=8192 python scripts/code_cache_microbench.py
"""

from __future__ import annotations

import gc
import os
from pathlib import Path
import statistics
import subprocess  # nosec B404
import sys
import tempfile
import textwrap
import time
from typing import Callable
from typing import cast

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import _memalloc


REPEATS = 3
MAX_FRAMES = 64
HEAP_SAMPLE_SIZE = 256
ALLOC_BYTES = 512
ALLOCS_PER_FN_PER_PASS = 1
PASSES_PER_SCENARIO = 4


def _build_unique_fns(n: int) -> list[Callable[[], bytearray]]:
    """Compile n distinct functions, each with its own PyCodeObject.

    Generated via `exec` with unique source per function -> CPython creates a
    distinct code object per function. Returning the local function ref keeps
    it alive.
    """
    fns: list[Callable[[], bytearray]] = []
    for i in range(n):
        ns: dict[str, Callable[[], bytearray]] = {}
        src = textwrap.dedent(
            f"""
            def fn_{i}(alloc_bytes={ALLOC_BYTES}):
                return bytearray(alloc_bytes)
            """
        )
        exec(src, ns)  # nosec B102 - generated benchmark code, local-only script
        fns.append(ns[f"fn_{i}"])
    return fns


def _drive(fns: list[Callable[[], bytearray]], passes: int) -> int:
    """Round-robin call every fn in `fns`, `passes` times. Returns total calls.

    Holds a tiny ring buffer (last 8) of returned bytearrays to keep some live
    set in allocs_m without retaining everything.
    """
    ring: list[object] = [None] * 8
    j = 0
    n = len(fns)
    total = 0
    for _ in range(passes):
        for i in range(n):
            for _k in range(ALLOCS_PER_FN_PER_PASS):
                ring[j] = fns[i]()
                j = (j + 1) % 8
                total += 1
    return total


def _setup_ddup(tmp_dir: Path, name: str) -> None:
    ddup.config(
        service=f"code-cache-bench-{name}",
        version="bench",
        env="bench",
        output_filename=str(tmp_dir / name),
    )
    ddup.start()


def _time_run(fns: list[Callable[[], bytearray]], cache_enabled: bool) -> tuple[int, int, dict[str, int], list[int]]:
    """Run one timed pass. Returns (elapsed_ns, total_calls, cache_stats_or_empty, per_set_or_empty)."""
    if cache_enabled:
        _memalloc.code_cache_enable()
    else:
        _memalloc.code_cache_disable()
    # Warmup so caches and JITs are hot; on cache-off side this still runs the
    # full slow path so it's fair.
    _drive(fns, passes=1)
    if cache_enabled:
        _memalloc.code_cache_reset_counters()
    gc.collect()
    t0 = time.perf_counter_ns()
    calls = _drive(fns, passes=PASSES_PER_SCENARIO)
    t1 = time.perf_counter_ns()
    stats: dict[str, int] = cast("dict[str, int]", _memalloc.code_cache_stats() or {})
    per_set: list[int] = _memalloc.code_cache_per_set_stats() or []
    return (t1 - t0, calls, stats, per_set)


def _scenario_ab(name: str, unique_fns: int, tmp_dir: Path) -> dict[str, object]:
    """A/B time the workload with cache enabled vs disabled."""
    fns = _build_unique_fns(unique_fns)
    on_ns: list[int] = []
    off_ns: list[int] = []
    on_stats: dict[str, int] = {}
    on_per_set: list[int] = []
    calls = 0
    for repeat in range(REPEATS):
        # ON
        _setup_ddup(tmp_dir, f"{name}_on_{repeat}")
        _memalloc.start(MAX_FRAMES, HEAP_SAMPLE_SIZE, True)
        try:
            elapsed, calls, on_stats, on_per_set = _time_run(fns, cache_enabled=True)
            on_ns.append(elapsed)
            _memalloc.heap()
        finally:
            _memalloc.stop()
            ddup.upload()

        # OFF
        _setup_ddup(tmp_dir, f"{name}_off_{repeat}")
        _memalloc.start(MAX_FRAMES, HEAP_SAMPLE_SIZE, True)
        try:
            elapsed, calls, _, _ = _time_run(fns, cache_enabled=False)
            off_ns.append(elapsed)
            _memalloc.heap()
        finally:
            _memalloc.code_cache_enable()
            _memalloc.stop()
            ddup.upload()

    med_on = statistics.median(on_ns)
    med_off = statistics.median(off_ns)
    speedup = med_off / med_on if med_on > 0 else float("nan")
    hits = on_stats.get("hits", 0)
    misses = on_stats.get("misses", 0)
    hit_rate = (100.0 * hits / (hits + misses)) if (hits + misses) > 0 else 0.0
    return {
        "name": name,
        "unique_fns": unique_fns,
        "calls": calls,
        "med_on_ms": med_on / 1e6,
        "med_off_ms": med_off / 1e6,
        "ns_per_call_on": med_on / max(calls, 1),
        "ns_per_call_off": med_off / max(calls, 1),
        "speedup": speedup,
        "hit_rate": hit_rate,
        "evictions": on_stats.get("evictions", 0),
        "capacity": on_stats.get("capacity", 0),
        "occupancy": on_per_set,
    }


def _print(r: dict[str, object]) -> None:
    occ = cast("list[int]", r.get("occupancy") or [])
    occ_str = "[" + ",".join(str(x) for x in occ) + "]" if occ else "-"
    print(
        f"  {r['name']:>8s}  unique={r['unique_fns']:>6d}  "
        f"calls={r['calls']:>7d}  "
        f"OFF={r['ns_per_call_off']:7.0f}ns  "
        f"ON={r['ns_per_call_on']:7.0f}ns  "
        f"speedup={r['speedup']:5.2f}x  "
        f"hit={r['hit_rate']:5.1f}%  "
        f"evict={r['evictions']:>8d}  "
        f"cap={r['capacity']}  "
        f"occ={occ_str}"
    )


def main() -> None:
    print(
        f"code-cache A/B microbench  repeats={REPEATS}  max_frames={MAX_FRAMES}  "
        f"sample_size={HEAP_SAMPLE_SIZE}  passes_per_scenario={PASSES_PER_SCENARIO}"
    )

    def _git(*args: str) -> str:
        # Fixed git args, no shell input; local dev provenance only.
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, check=False)  # nosec B603 B607
            return out.stdout.strip() or "?"
        except Exception:
            return "?"

    print(
        f"python={sys.version.split()[0]}  branch={_git('branch', '--show-current')}  "
        f"commit={_git('rev-parse', '--short', 'HEAD')}"
    )
    cap_env = os.environ.get("DD_PROFILING_MEMALLOC_CODE_CACHE_SIZE", "(default)")
    print(f"DD_PROFILING_MEMALLOC_CODE_CACHE_SIZE={cap_env}")
    print()
    print("OFF: ns/call without cache  ON: ns/call with cache  speedup = OFF / ON")
    print()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        scenarios = [
            ("tiny", 10),
            ("small", 1_000),
            ("mid", 16_000),
            ("full", 32_000),
            ("churn", 64_000),
        ]
        for name, n in scenarios:
            try:
                r = _scenario_ab(name, unique_fns=n, tmp_dir=tmp_dir)
                _print(r)
            except MemoryError:
                print(f"  {name:>8s}  unique={n:>6d}  MemoryError - skip")


if __name__ == "__main__":
    main()
