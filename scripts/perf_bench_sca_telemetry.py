#!/usr/bin/env python
"""Benchmark: telemetry dependency reporting with SCA enabled vs disabled.

Compares performance of:
- update_imported_dependencies (new module discovery)
- _report_dependencies (periodic reporting with re-report logic)
- to_telemetry_dict serialization
- attach_dependency_metadata (SCA hook path)

Usage:
    python scripts/perf_bench_sca_telemetry.py
"""

import gc
import statistics
import time
import tracemalloc

from ddtrace.internal.telemetry.dependency import DependencyEntry
from ddtrace.internal.telemetry.dependency import attach_reachability_metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entries(n, sca_enabled=False):
    """Create n DependencyEntry objects, optionally with SCA metadata=[]."""
    entries = {}
    for i in range(n):
        name = f"package-{i}"
        meta = [] if sca_enabled else None
        entries[name] = DependencyEntry(name=name, version=f"{i}.0.0", metadata=meta)
    return entries


def _make_entries_with_metadata(n, cves_per_entry=2):
    """Create n entries each with some metadata already attached."""
    entries = {}
    for i in range(n):
        name = f"package-{i}"
        entry = DependencyEntry(name=name, version=f"{i}.0.0", metadata=[])
        entry.mark_initial_sent()
        for c in range(cves_per_entry):
            entry.add_metadata(f"CVE-{i}-{c}", f"mod{i}", f"func{c}", c + 1)
        entry.mark_all_metadata_sent()
        entries[name] = entry
    return entries


def bench(func, warmup=5, iterations=200):
    """Run func() `iterations` times and return timing stats."""
    for _ in range(warmup):
        func()
    gc.disable()
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        func()
        times.append(time.perf_counter_ns() - t0)
    gc.enable()
    times_us = [t / 1000 for t in times]
    return {
        "median_us": statistics.median(times_us),
        "p95_us": sorted(times_us)[int(len(times_us) * 0.95)],
        "stdev_us": statistics.stdev(times_us) if len(times_us) > 1 else 0,
    }


def bench_memory(func, iterations=50):
    """Measure peak memory of func() over iterations."""
    tracemalloc.start()
    for _ in range(iterations):
        func()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def print_row(label, stats_off, stats_on):
    delta = ((stats_on["median_us"] / stats_off["median_us"]) - 1) * 100 if stats_off["median_us"] else 0
    sign = "+" if delta >= 0 else ""
    print(
        f"  {label:<45} "
        f"{stats_off['median_us']:>10.1f} "
        f"{stats_on['median_us']:>10.1f} "
        f"{sign}{delta:>7.1f}% "
        f"{stats_off['p95_us']:>10.1f} "
        f"{stats_on['p95_us']:>10.1f}"
    )


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def bench_to_telemetry_dict(n):
    """Benchmark to_telemetry_dict for n entries."""
    entries_off = _make_entries(n, sca_enabled=False)
    entries_on = _make_entries(n, sca_enabled=True)

    def run_off():
        for e in entries_off.values():
            e.to_telemetry_dict()

    def run_on():
        for e in entries_on.values():
            e.to_telemetry_dict()

    return bench(run_off), bench(run_on)


def bench_to_telemetry_dict_with_metadata(n, cves=2):
    """Benchmark to_telemetry_dict(include_all_metadata=True) with metadata."""
    entries = _make_entries_with_metadata(n, cves)

    def run():
        for e in entries.values():
            e.to_telemetry_dict(include_all_metadata=True)

    stats = bench(run)
    return stats


def bench_attach_metadata(n):
    """Benchmark attaching metadata to n entries."""

    def run_off():
        entries = _make_entries(n, sca_enabled=False)
        for i in range(n):
            attach_reachability_metadata(entries, f"package-{i}", "CVE-1", "mod", "func", 1)

    def run_on():
        entries = _make_entries(n, sca_enabled=True)
        for i in range(n):
            attach_reachability_metadata(entries, f"package-{i}", "CVE-1", "mod", "func", 1)

    return bench(run_off), bench(run_on)


def bench_rereport_scan(n, pct_with_unsent=10):
    """Benchmark scanning for entries with unsent metadata (re-report path)."""
    entries = _make_entries_with_metadata(n, cves_per_entry=2)
    # Mark a percentage as having unsent metadata
    keys = list(entries.keys())
    for k in keys[: max(1, n * pct_with_unsent // 100)]:
        entries[k].add_metadata("CVE-NEW", "new.mod", "new_func", 99)

    def run():
        result = []
        for entry in entries.values():
            if entry._initial_report_sent and entry.has_unsent_metadata():
                result.append(entry.to_telemetry_dict(include_all_metadata=True))

    return bench(run)


def bench_enable_sca_metadata(n):
    """Benchmark enable_sca_metadata (setting None→[] on all entries)."""

    def run():
        entries = _make_entries(n, sca_enabled=False)
        for entry in entries.values():
            if entry.metadata is None:
                entry.metadata = []

    return bench(run)


def bench_memory_comparison(n):
    """Compare memory usage between SCA disabled and enabled entries."""

    def create_off():
        return _make_entries(n, sca_enabled=False)

    def create_on():
        return _make_entries(n, sca_enabled=True)

    peak_off = bench_memory(create_off, iterations=20)
    peak_on = bench_memory(create_on, iterations=20)
    return peak_off, peak_on


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 110)
    print("SCA Telemetry Performance Benchmark")
    print("=" * 110)

    for n in [50, 200, 500, 1000]:
        print(f"\n--- {n} dependencies ---")
        print(f"  {'Benchmark':<45} {'SCA OFF (μs)':>12} {'SCA ON (μs)':>12} {'Δ':>9} {'p95 OFF':>10} {'p95 ON':>10}")
        print("  " + "-" * 105)

        # to_telemetry_dict
        off, on = bench_to_telemetry_dict(n)
        print_row(f"to_telemetry_dict ({n} entries)", off, on)

        # attach_metadata
        off, on = bench_attach_metadata(n)
        print_row(f"attach_metadata ({n} entries)", off, on)

        # re-report scan (SCA-only, 10% with unsent metadata)
        stats = bench_rereport_scan(n)
        print(
            f"  {'re-report scan (10% unsent)':<45} "
            f"{'n/a':>12} "
            f"{stats['median_us']:>10.1f}μs "
            f"{'':>9} "
            f"{'':>10} "
            f"{stats['p95_us']:>10.1f}"
        )

        # enable_sca_metadata
        stats = bench_enable_sca_metadata(n)
        print(
            f"  {'enable_sca_metadata (None→[])':<45} "
            f"{'n/a':>12} "
            f"{stats['median_us']:>10.1f}μs "
            f"{'':>9} "
            f"{'':>10} "
            f"{stats['p95_us']:>10.1f}"
        )

    # to_telemetry_dict with metadata (SCA-only scenario)
    print("\n--- Serialization with metadata (SCA active) ---")
    for n in [50, 200, 500]:
        for cves in [1, 5]:
            stats = bench_to_telemetry_dict_with_metadata(n, cves)
            print(
                f"  to_telemetry_dict({n} entries, {cves} CVEs each): "
                f"median={stats['median_us']:.1f}μs  p95={stats['p95_us']:.1f}μs"
            )

    # Memory comparison
    print("\n--- Memory comparison ---")
    for n in [200, 1000]:
        peak_off, peak_on = bench_memory_comparison(n)
        delta_kb = (peak_on - peak_off) / 1024
        print(f"  {n} entries: SCA OFF={peak_off / 1024:.1f}KB  SCA ON={peak_on / 1024:.1f}KB  Δ={delta_kb:+.1f}KB")

    print("\n" + "=" * 110)
    print("Done.")
