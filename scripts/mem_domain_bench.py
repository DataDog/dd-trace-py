#!/usr/bin/env python3
"""
Measure heap-space coverage before/after MEM domain tracking.

Usage:
  # Capture a profile (run on each branch):
  python scripts/mem_domain_bench.py

  # Compare two captures:
  python scripts/mem_domain_bench.py --compare /tmp/before.pprof

Before/after procedure:
  # OBJ-only baseline (MEM domain disabled via env var):
  DD_PROFILING_MEM_DOMAIN_ENABLED=false python scripts/mem_domain_bench.py
  cp /tmp/mem_bench.<pid>.0.pprof /tmp/before.pprof

  # MEM domain enabled:
  DD_PROFILING_MEM_DOMAIN_ENABLED=true python scripts/mem_domain_bench.py --compare /tmp/before.pprof
"""

import argparse
import os
from pathlib import Path
import sys
import tempfile
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from ddtrace.profiling.collector.memalloc import MemoryCollector


# Keep pools alive so heap-space sees live allocations at snapshot time.
_pool_obj: list = []
_pool_mem: list = []
_pool_raw: list = []

TARGET_BYTES: int = 100 * 1024 * 1024  # 100 MB per domain


# ── Workload ───────────────────────────────────────────────────────────────
# Each domain lives in its own function so pprof groups heap-space bytes
# by call site — enabling before/after comparison by function name.


def _alloc_obj(target_bytes: int) -> None:
    """OBJ domain: small bytes objects via PyObject_Malloc."""
    chunk: int = 64
    for _ in range(target_bytes // chunk):
        _pool_obj.append(bytes(chunk))


def _alloc_mem(target_bytes: int) -> None:
    """MEM domain: array.array internal buffers + list ob_item via PyMem_Calloc.

    Uses None as list elements (singleton, no per-element OBJ allocation) and
    [None]*N (single PyMem_Calloc for ob_item) to keep this pure MEM domain.
    """
    import array

    item_count: int = 65536
    n_arrays: int = (target_bytes // 2) // item_count
    for _ in range(n_arrays):
        _pool_mem.append(array.array("B", bytes(item_count)))
    # [None] * N: ob_item allocated in one shot via PyList_New → PyMem_Calloc(N, 8)
    n_ptrs: int = (target_bytes // 2) // 8
    _pool_mem.append([None] * n_ptrs)


def _alloc_raw(target_bytes: int) -> None:
    """RAW domain: large bytearray chunks via malloc (NOT tracked by our hook)."""
    chunk_size: int = 1024 * 1024
    for _ in range(target_bytes // chunk_size):
        _pool_raw.append(bytearray(chunk_size))


def _run_workload() -> None:
    mb: int = TARGET_BYTES // 1024 // 1024
    print(f"Allocating OBJ domain (~{mb} MB, small bytes objects)...")
    _alloc_obj(TARGET_BYTES)

    print(f"Allocating MEM domain (~{mb} MB, array.array + list appends)...")
    _alloc_mem(TARGET_BYTES)

    print(f"Allocating RAW domain (~{mb} MB, 1 MB bytearray chunks — not tracked)...")
    _alloc_raw(TARGET_BYTES)


# ── Profiler helpers ───────────────────────────────────────────────────────


def _start_profiler(output_prefix: str, mem_domain_enabled: bool) -> "MemoryCollector":
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import memalloc

    ddup.config(
        env="dev",
        service="mem_domain_bench",
        version="local",
        output_filename=output_prefix,
    )
    ddup.start()

    mc = memalloc.MemoryCollector(heap_sample_size=512 * 1024, mem_domain_enabled=mem_domain_enabled)
    mc.start()
    return mc


def _snapshot_and_upload(mc: "MemoryCollector", output_prefix: str) -> Path:
    from ddtrace.internal.datadog.profiling import ddup

    mc.snapshot()
    mc.stop()
    ddup.upload()

    profs: list[Path] = sorted(
        Path(output_prefix).parent.glob(Path(output_prefix).name + ".*.pprof"),
        key=lambda p: int(p.stem.rsplit(".", 1)[-1]),
    )
    if not profs:
        sys.exit("ERROR: No .pprof files found — ddup.upload() may have failed.")
    return profs[-1]


# ── Profile parsing ────────────────────────────────────────────────────────


def _parse_profile(pprof_path: str) -> tuple[dict[str, int], int, int]:
    """Return (top_frame_bytes, n_heap_samples, total_heap_bytes)."""
    repo_root: Path = Path(__file__).parent.parent
    # pprof_utils uses absolute imports like `tests.profiling.collector.lock_utils`,
    # so we need the repo root on sys.path, not the subdirectory.
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tests.profiling.collector import pprof_utils

    profile = pprof_utils.parse_profile(pprof_path)

    try:
        heap_idx: int = pprof_utils.get_sample_type_index(profile, "heap-space")
    except StopIteration:
        sys.exit("ERROR: 'heap-space' sample type not found in profile. Is heap profiling enabled?")

    by_func: dict[str, int] = {}
    total_bytes: int = 0
    n_samples: int = 0

    for sample in profile.sample:
        val: int = sample.value[heap_idx]
        if val <= 0 or not sample.location_id:
            continue
        n_samples += 1
        total_bytes += val

        # Attribute to the innermost (top) Python frame.
        loc = pprof_utils.get_location_with_id(profile, sample.location_id[0])
        if loc.line:
            fn = pprof_utils.get_function_with_id(profile, loc.line[0].function_id)
            name: str = profile.string_table[fn.name]
            by_func[name] = by_func.get(name, 0) + val

    return by_func, n_samples, total_bytes


# ── Output ─────────────────────────────────────────────────────────────────


def _print_summary(by_func: dict[str, int], n_samples: int, total_bytes: int, label: str = "") -> None:
    sep: str = "─" * 68
    print(f"\n{sep}")
    if label:
        print(f"  {label}")
        print(f"{sep}")
    print(f"  Heap-space samples : {n_samples}")
    print(f"  Total tracked      : {total_bytes / 1024 / 1024:.1f} MB")
    print("\n  Top 15 frames (top-of-stack, by heap-space bytes):")
    top = sorted(by_func.items(), key=lambda x: -x[1])[:15]
    for name, val in top:
        mb: float = val / 1024 / 1024
        bar: str = "█" * min(28, max(1, int(mb / 4)))
        print(f"    {name:<46s}  {mb:7.1f} MB  {bar}")
    print(f"{sep}")


def _print_compare(
    before: dict[str, int], after: dict[str, int], n_before: int, n_after: int, total_before: int, total_after: int
) -> None:
    sep: str = "─" * 82
    all_funcs: list[str] = sorted(
        set(before) | set(after),
        key=lambda f: -(after.get(f, 0) + before.get(f, 0)),
    )[:20]

    print(f"\n{sep}")
    print("  Heap-space diff  (before → after)")
    print(f"{sep}")
    print(f"  {'Function':<46s}  {'Before':>9s}  {'After':>9s}  {'Delta':>9s}")
    print(f"  {'─' * 46}  {'─' * 9}  {'─' * 9}  {'─' * 9}")
    for fn in all_funcs:
        b_mb: float = before.get(fn, 0) / 1024 / 1024
        a_mb: float = after.get(fn, 0) / 1024 / 1024
        d_mb: float = a_mb - b_mb
        marker: str = " ▲" if d_mb > 0.5 else (" ▼" if d_mb < -0.5 else "  ")
        print(f"  {fn:<46s}  {b_mb:8.1f}M  {a_mb:8.1f}M  {marker}{abs(d_mb):7.1f}M")
    print(f"  {'─' * 46}  {'─' * 9}  {'─' * 9}  {'─' * 9}")
    d_total: float = (total_after - total_before) / 1024 / 1024
    marker_t: str = " ▲" if d_total > 0.5 else (" ▼" if d_total < -0.5 else "  ")
    print(
        f"  {'TOTAL':<46s}  "
        f"{total_before / 1024 / 1024:8.1f}M  "
        f"{total_after / 1024 / 1024:8.1f}M  "
        f"{marker_t}{abs(d_total):7.1f}M"
    )
    print(f"\n  Samples: {n_before} → {n_after}  ({n_after - n_before:+d})")
    print(f"{sep}")


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--compare", metavar="BEFORE_PPROF", help="Compare current run against a saved baseline profile"
    )
    parser.add_argument(
        "--heap-sample-size",
        type=int,
        default=512 * 1024,
        metavar="BYTES",
        help="Heap sampling interval in bytes (default: 524288 = 512 KB)",
    )
    parser.add_argument(
        "--mem-domain",
        action="store_true",
        default=False,
        help="Enable MEM-domain tracking (DD_PROFILING_MEM_DOMAIN_ENABLED=true)",
    )
    args: argparse.Namespace = parser.parse_args()

    tmp: str = tempfile.mkdtemp(prefix="mem_bench_")
    output_prefix: str = os.path.join(tmp, "mem_bench")

    mem_domain: bool = args.mem_domain or os.environ.get("DD_PROFILING_MEM_DOMAIN_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    print(
        f"Starting profiler (heap_sample_size={args.heap_sample_size // 1024} KB, mem_domain_enabled={mem_domain})..."
    )
    mc = _start_profiler(output_prefix, mem_domain_enabled=mem_domain)

    _run_workload()

    print("\nTaking heap snapshot...")
    pprof_path: Path = _snapshot_and_upload(mc, output_prefix)
    print(f"Profile written: {pprof_path}")
    print(f"\nTo save for comparison:\n  cp {pprof_path} /tmp/before.pprof\n")

    by_func, n_samples, total_bytes = _parse_profile(str(pprof_path))
    _print_summary(by_func, n_samples, total_bytes, label="Current run")

    if args.compare:
        print(f"\nLoading baseline: {args.compare}")
        before_func, before_n, before_total = _parse_profile(args.compare)
        _print_compare(before_func, by_func, before_n, n_samples, before_total, total_bytes)


if __name__ == "__main__":
    main()
