#!/usr/bin/env python
"""Benchmark: telemetry heartbeat cycle (collect_report) with SCA enabled vs disabled.

Simulates realistic heartbeat cycles at scale to measure the overhead of
SCA reachability on the periodic telemetry reporting path.

Scenarios per scale (1_000 / 10_000 dependencies):
  Loop 1 — First heartbeat:  all dependencies are new (initial report)
  Loop 2 — Idle heartbeat:   no new deps, no new metadata (empty payload)
  Loop 3 — CVE registration: some deps have CVEs registered (reached=[])
  Loop 4 — SCA hits:         some deps have triggered reachability hooks

Each scenario is run with SCA OFF (metadata=None) and SCA ON (metadata=[]),
and we compare execution time and peak memory.

Usage:
    python scripts/perf_bench_heartbeat_cycles.py
"""

import gc
import statistics
import sys
import time
import tracemalloc
from unittest.mock import patch

from ddtrace.internal.settings._config import config as tracer_config
from ddtrace.internal.telemetry.dependency import DependencyEntry
from ddtrace.internal.telemetry.dependency import attach_reachability_metadata
from ddtrace.internal.telemetry.dependency import register_cve_metadata
from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER_WIDTH = 135


def _populate_tracker(tracker: DependencyTracker, n: int, sca_enabled: bool) -> None:
    """Pre-populate a tracker with n dependencies, as if discovered by the first heartbeat."""
    if sca_enabled:
        tracer_config._sca_enabled = True
    for i in range(n):
        name = f"package-{i}"
        meta = [] if sca_enabled else None
        tracker._imported_dependencies[name] = DependencyEntry(name=name, version=f"{i}.0.0", metadata=meta)
        tracker._imported_dependencies[name].mark_initial_sent()
        tracker._imported_dependencies[name].mark_all_metadata_sent()


def _register_cves(tracker: DependencyTracker, n_deps: int, pct: int, cves_per_dep: int) -> int:
    """Register CVEs (reached=[]) on pct% of the first n_deps dependencies.

    Returns the number of CVEs registered.
    """
    count = max(1, n_deps * pct // 100)
    total = 0
    for i in range(count):
        name = f"package-{i}"
        for c in range(cves_per_dep):
            register_cve_metadata(tracker._imported_dependencies, name, f"CVE-{i}-{c}")
            total += 1
    return total


def _attach_hits(tracker: DependencyTracker, n_deps: int, pct: int, cves_per_dep: int) -> int:
    """Simulate SCA hook hits on pct% of deps that already have CVEs registered.

    Returns the number of hits attached.
    """
    count = max(1, n_deps * pct // 100)
    total = 0
    for i in range(count):
        name = f"package-{i}"
        for c in range(cves_per_dep):
            attach_reachability_metadata(
                tracker._imported_dependencies,
                name,
                f"CVE-{i}-{c}",
                f"myapp.views{i}",
                f"handle_{c}",
                10 + c,
            )
            total += 1
    return total


def _make_tracker_for_loop(n: int, sca_enabled: bool, phase: str, pct_cve: int, cves_per_dep: int) -> DependencyTracker:
    """Build a tracker in the right state for the given benchmark phase."""
    tracker = DependencyTracker()
    if phase == "first":
        # All deps are new: NOT pre-populated (will be "discovered" by collect_report mock)
        if sca_enabled:
            tracer_config._sca_enabled = True
        return tracker
    # For all other phases, pre-populate as already reported
    _populate_tracker(tracker, n, sca_enabled)
    if phase == "cve_registration":
        _register_cves(tracker, n, pct_cve, cves_per_dep)
    elif phase == "sca_hits":
        _register_cves(tracker, n, pct_cve, cves_per_dep)
        # Mark CVE registrations as sent first (simulates previous heartbeat)
        for entry in tracker._imported_dependencies.values():
            entry.mark_all_metadata_sent()
        _attach_hits(tracker, n, pct_cve, cves_per_dep)
    return tracker


def _collect_report_no_new_modules(tracker: DependencyTracker) -> list | None:
    """Run collect_report with no new module discovery (mocked).

    This isolates the re-report scan + serialization from the sys.modules overhead.
    NOTE: Only used for one-off calls (payload size, memory). For timing benchmarks,
    use _idle_mock_ctx() / _new_modules_mock_ctx() to set up mocks once outside the loop.
    """
    with _idle_mock_ctx():
        return tracker.collect_report()


def _collect_report_new_modules(tracker: DependencyTracker, module_names: set[str], dists: dict) -> list | None:
    """Run collect_report simulating new module discovery.

    NOTE: Only used for one-off calls (payload size). For timing benchmarks,
    use _new_modules_mock_ctx() to set up mocks once outside the loop.
    """
    with _new_modules_mock_ctx(module_names, dists):
        return tracker.collect_report()


class _idle_mock_ctx:
    """Context manager that patches modules/config once for idle heartbeat benchmarks.

    Avoids per-iteration mock setup/teardown overhead in timing loops.
    """

    def __enter__(self):
        self._p1 = patch("ddtrace.internal.telemetry.dependency_tracker.modules")
        self._p2 = patch("ddtrace.internal.telemetry.dependency_tracker.config")
        mock_modules = self._p1.__enter__()
        mock_config = self._p2.__enter__()
        mock_config.DEPENDENCY_COLLECTION = True
        mock_modules.get_newly_imported_modules.return_value = set()
        return self

    def __exit__(self, *args):
        self._p2.__exit__(*args)
        self._p1.__exit__(*args)


class _new_modules_mock_ctx:
    """Context manager that patches modules/config/get_dist once for first-heartbeat benchmarks."""

    def __init__(self, module_names, dists):
        self._module_names = module_names
        self._dists = dists

    def __enter__(self):
        def fake_get_dist(module_name):
            return self._dists.get(module_name)

        self._p1 = patch("ddtrace.internal.telemetry.dependency_tracker.modules")
        self._p2 = patch("ddtrace.internal.telemetry.dependency_tracker.config")
        self._p3 = patch(
            "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
            side_effect=fake_get_dist,
        )
        mock_modules = self._p1.__enter__()
        mock_config = self._p2.__enter__()
        self._p3.__enter__()
        mock_config.DEPENDENCY_COLLECTION = True
        mock_modules.get_newly_imported_modules.return_value = self._module_names
        return self

    def __exit__(self, *args):
        self._p3.__exit__(*args)
        self._p2.__exit__(*args)
        self._p1.__exit__(*args)


# ---------------------------------------------------------------------------
# Timing / Memory helpers
# ---------------------------------------------------------------------------


def bench_time(func, warmup=3, iterations=100):
    """Run func() `iterations` times and return timing stats in microseconds."""
    for _ in range(warmup):
        func()
    gc.disable()
    try:
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            func()
            times.append(time.perf_counter_ns() - t0)
    finally:
        gc.enable()
    times_us = [t / 1000 for t in times]
    return {
        "median_us": statistics.median(times_us),
        "p95_us": sorted(times_us)[int(len(times_us) * 0.95)],
        "min_us": min(times_us),
        "max_us": max(times_us),
    }


def bench_memory(setup_func, measure_func, iterations=10):
    """Measure peak memory allocated during measure_func over iterations.

    setup_func is called once before measurement to create the state.
    measure_func receives the result of setup_func.
    """
    state = setup_func()
    tracemalloc.start()
    for _ in range(iterations):
        measure_func(state)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


# ---------------------------------------------------------------------------
# Benchmark scenarios
# ---------------------------------------------------------------------------


def bench_loop1_first_heartbeat(n: int, sca_enabled: bool, iterations: int = 50):
    """Loop 1: First heartbeat — all N dependencies discovered as new."""
    module_names = {f"mod-{i}" for i in range(n)}
    dists = {f"mod-{i}": (f"package-{i}", f"{i}.0.0") for i in range(n)}

    with _new_modules_mock_ctx(module_names, dists):

        def run():
            tracker = DependencyTracker()
            if sca_enabled:
                tracer_config._sca_enabled = True
            tracker.collect_report()

        return bench_time(run, warmup=2, iterations=iterations)


def bench_loop2_idle_heartbeat(n: int, sca_enabled: bool, iterations: int = 200):
    """Loop 2: Idle heartbeat — all deps already reported, nothing new."""
    tracker = _make_tracker_for_loop(n, sca_enabled, "idle", 0, 0)

    with _idle_mock_ctx():

        def run():
            tracker.collect_report()

        return bench_time(run, iterations=iterations)


def bench_loop3_cve_registration(n: int, sca_enabled: bool, pct_cve: int, cves_per_dep: int, iterations: int = 50):
    """Loop 3: CVE registration heartbeat — pct% of deps have CVEs with reached=[]."""
    if not sca_enabled:
        # SCA OFF has no CVE registration; run idle for comparison
        return bench_loop2_idle_heartbeat(n, sca_enabled, iterations)

    with _idle_mock_ctx():

        def run():
            tracker = _make_tracker_for_loop(n, sca_enabled, "cve_registration", pct_cve, cves_per_dep)
            tracker.collect_report()

        return bench_time(run, warmup=2, iterations=iterations)


def bench_loop4_sca_hits(n: int, sca_enabled: bool, pct_cve: int, cves_per_dep: int, iterations: int = 50):
    """Loop 4: SCA hit heartbeat — pct% of deps have reachability hits."""
    if not sca_enabled:
        return bench_loop2_idle_heartbeat(n, sca_enabled, iterations)

    with _idle_mock_ctx():

        def run():
            tracker = _make_tracker_for_loop(n, sca_enabled, "sca_hits", pct_cve, cves_per_dep)
            tracker.collect_report()

        return bench_time(run, warmup=2, iterations=iterations)


# ---------------------------------------------------------------------------
# Legacy (main) benchmarks — simulate old code path without DependencyTracker
# ---------------------------------------------------------------------------


def bench_loop1_legacy(n: int, iterations: int = 50):
    """Legacy (main): First heartbeat — all N dependencies discovered."""
    module_names = {f"mod-{i}" for i in range(n)}
    dists = {f"mod-{i}": (f"package-{i}", f"{i}.0.0") for i in range(n)}

    def fake_get_dist(module_name):
        return dists.get(module_name)

    with patch(
        "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
        side_effect=fake_get_dist,
    ):

        def run():
            already_imported: dict[str, DependencyEntry] = {}
            update_imported_dependencies(already_imported, module_names)

        return bench_time(run, warmup=2, iterations=iterations)


def bench_loop_idle_legacy(n: int, iterations: int = 200):
    """Legacy (main): Idle heartbeat — nothing new, no re-report logic."""
    already_imported: dict[str, DependencyEntry] = {}
    for i in range(n):
        name = f"package-{i}"
        already_imported[name] = DependencyEntry(name=name, version=f"{i}.0.0", metadata=None)

    def run():
        update_imported_dependencies(already_imported, set())

    return bench_time(run, iterations=iterations)


def bench_memory_heartbeat(n: int, sca_enabled: bool, phase: str, pct_cve: int, cves_per_dep: int):
    """Measure peak memory for a single collect_report cycle."""

    def setup():
        return _make_tracker_for_loop(n, sca_enabled, phase, pct_cve, cves_per_dep)

    def measure(tracker):
        _collect_report_no_new_modules(tracker)

    return bench_memory(setup, measure, iterations=5)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def fmt_us(val):
    """Format microseconds with appropriate unit."""
    if val >= 1_000_000:
        return f"{val / 1_000_000:.2f}s"
    if val >= 1_000:
        return f"{val / 1_000:.2f}ms"
    return f"{val:.1f}us"


def print_comparison(label, stats_main, stats_off, stats_on):
    delta = ((stats_on["median_us"] / max(stats_off["median_us"], 0.001)) - 1) * 100
    sign = "+" if delta >= 0 else ""
    print(
        f"  {label:<45} "
        f"{fmt_us(stats_main['median_us']):>12} "
        f"{fmt_us(stats_off['median_us']):>10} "
        f"{fmt_us(stats_on['median_us']):>10} "
        f"{sign}{delta:>7.1f}% "
        f"{fmt_us(stats_off['p95_us']):>10} "
        f"{fmt_us(stats_on['p95_us']):>10}"
    )


def print_memory(label, peak_off, peak_on):
    delta_kb = (peak_on - peak_off) / 1024
    print(f"  {label:<55} {peak_off / 1024:>9.1f}KB {peak_on / 1024:>9.1f}KB {delta_kb:>+9.1f}KB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * _HEADER_WIDTH)
    print("SCA Telemetry Heartbeat Cycle Benchmark")
    print(f"Python {sys.version.split()[0]}")
    print("=" * _HEADER_WIDTH)

    dep_counts = [1_000, 10_000]
    pct_cve = 5  # 5% of deps have CVEs
    cves_per_dep = 2

    for n in dep_counts:
        n_with_cves = max(1, n * pct_cve // 100)
        total_cves = n_with_cves * cves_per_dep

        print(f"\n{'=' * _HEADER_WIDTH}")
        print(f"  {n:,} dependencies | {pct_cve}% with CVEs ({n_with_cves:,} deps, {total_cves:,} CVEs)")
        print(f"{'=' * _HEADER_WIDTH}")

        # ---- Timing ----
        print(
            f"\n  {'Benchmark':<45} {'SCA OFF(main)':>12} {'SCA OFF':>10} {'SCA ON':>10} "
            f"{'Delta':>9} {'p95 OFF':>10} {'p95 ON':>10}"
        )
        print("  " + "-" * (_HEADER_WIDTH - 4))

        # Loop 1: First heartbeat (all new)
        iters = 20 if n >= 10_000 else 50
        main = bench_loop1_legacy(n, iterations=iters)
        off = bench_loop1_first_heartbeat(n, sca_enabled=False, iterations=iters)
        on = bench_loop1_first_heartbeat(n, sca_enabled=True, iterations=iters)
        print_comparison(f"Loop 1: First heartbeat ({n:,} new deps)", main, off, on)

        # Loop 2: Idle heartbeat (nothing to do)
        iters = 100 if n >= 10_000 else 200
        main = bench_loop_idle_legacy(n, iterations=iters)
        off = bench_loop2_idle_heartbeat(n, sca_enabled=False, iterations=iters)
        on = bench_loop2_idle_heartbeat(n, sca_enabled=True, iterations=iters)
        print_comparison("Loop 2: Idle heartbeat (0 changes)", main, off, on)

        # Loop 3: CVE registration (reached=[])
        iters = 20 if n >= 10_000 else 50
        main = bench_loop_idle_legacy(n, iterations=iters)
        off = bench_loop3_cve_registration(
            n, sca_enabled=False, pct_cve=pct_cve, cves_per_dep=cves_per_dep, iterations=iters
        )
        on = bench_loop3_cve_registration(
            n, sca_enabled=True, pct_cve=pct_cve, cves_per_dep=cves_per_dep, iterations=iters
        )
        print_comparison(f"Loop 3: CVE registration ({total_cves:,} CVEs)", main, off, on)

        # Loop 4: SCA hits (reached=[{{...}}])
        main = bench_loop_idle_legacy(n, iterations=iters)
        off = bench_loop4_sca_hits(n, sca_enabled=False, pct_cve=pct_cve, cves_per_dep=cves_per_dep, iterations=iters)
        on = bench_loop4_sca_hits(n, sca_enabled=True, pct_cve=pct_cve, cves_per_dep=cves_per_dep, iterations=iters)
        print_comparison(f"Loop 4: SCA hits ({total_cves:,} on {n_with_cves:,} deps)", main, off, on)

        # ---- Memory ----
        print(f"\n  {'Memory (peak per collect_report)':<55} {'SCA OFF':>10} {'SCA ON':>10} {'Delta':>10}")
        print("  " + "-" * (_HEADER_WIDTH - 4))

        for phase, label in [
            ("idle", "Idle heartbeat"),
            ("cve_registration", f"CVE registration ({total_cves:,} CVEs)"),
            ("sca_hits", f"SCA hits ({total_cves:,} hits)"),
        ]:
            peak_off = bench_memory_heartbeat(n, False, phase, pct_cve, cves_per_dep)
            peak_on = bench_memory_heartbeat(n, True, phase, pct_cve, cves_per_dep)
            print_memory(f"  {label}", peak_off, peak_on)

    # ---- Payload size comparison ----
    print(f"\n{'=' * _HEADER_WIDTH}")
    print("  Payload size comparison (JSON bytes)")
    print(f"{'=' * _HEADER_WIDTH}")

    for n in dep_counts:
        n_with_cves = max(1, n * pct_cve // 100)
        total_cves = n_with_cves * cves_per_dep

        print(f"\n  {n:,} dependencies:")
        for phase, label in [
            ("first", "First heartbeat (all new)"),
            ("idle", "Idle heartbeat"),
            ("cve_registration", f"CVE registration ({total_cves:,} CVEs)"),
            ("sca_hits", f"SCA hits ({total_cves:,} hits)"),
        ]:
            for sca_label, sca_on in [("OFF", False), ("ON", True)]:
                if phase == "first":
                    module_names = {f"mod-{i}" for i in range(n)}
                    dists = {f"mod-{i}": (f"package-{i}", f"{i}.0.0") for i in range(n)}
                    tracker = DependencyTracker()
                    if sca_on:
                        tracer_config._sca_enabled = True
                    result = _collect_report_new_modules(tracker, module_names, dists)
                else:
                    tracker = _make_tracker_for_loop(n, sca_on, phase, pct_cve, cves_per_dep)
                    result = _collect_report_no_new_modules(tracker)

                import json

                payload_bytes = len(json.dumps(result or []).encode())
                n_entries = len(result) if result else 0
                print(f"    SCA {sca_label:<3} {label:<45} entries={n_entries:<6} size={payload_bytes:>10,} bytes")

    print(f"\n{'=' * _HEADER_WIDTH}")
    print("Done.")


if __name__ == "__main__":
    main()
