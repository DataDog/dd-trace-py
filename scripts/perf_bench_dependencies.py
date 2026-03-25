#!/usr/bin/env python3
"""Benchmark: telemetry dependency functions — this branch vs main.

Measures time and memory of update_imported_dependencies and _report_dependencies
by inlining both the main (plain dict) and branch (DependencyEntry dataclass)
implementations to avoid native extension import issues.

Usage:
    export PYTHONPATH="$PWD:$PYTHONPATH"
    python3.12 scripts/perf_bench_dependencies.py
"""

from dataclasses import dataclass
from dataclasses import field
import gc
import statistics
import subprocess
import sys
import time
import tracemalloc
from typing import Optional


# ============================================================================
# Inlined implementation — MAIN branch (plain str values)
# ============================================================================


def _main_update(already_imported, new_modules, dist_lookup):
    """Main branch: dict[str, str], returns list of dicts."""
    deps = []
    for module_name in new_modules:
        dists = dist_lookup(module_name)
        if not dists:
            continue
        name, version = dists
        if name == "ddtrace":
            continue
        if name in already_imported:
            continue
        already_imported[name] = version
        deps.append({"name": name, "version": version})
    return deps


def _main_report(already_imported, modules_already_imported, new_module_names, dist_lookup):
    """Main branch _report_dependencies logic."""
    newly_imported = new_module_names - modules_already_imported
    modules_already_imported.update(new_module_names)
    if not newly_imported:
        return None
    return _main_update(already_imported, newly_imported, dist_lookup)


# ============================================================================
# Inlined implementation — THIS BRANCH (DependencyEntry storage, plain dict output)
# ============================================================================


@dataclass
class DependencyEntry:
    """Matches the actual branch code."""

    name: str
    version: str
    metadata: Optional[list] = None
    _initial_report_sent: bool = field(default=False, repr=False, compare=False)


def _branch_update(already_imported, new_modules, dist_lookup):
    """This branch: stores DependencyEntry, returns plain dicts."""
    deps = []
    for module_name in new_modules:
        dists = dist_lookup(module_name)
        if not dists:
            continue
        name, version = dists
        if name == "ddtrace":
            continue
        if name in already_imported:
            continue
        already_imported[name] = DependencyEntry(name=name, version=version)
        deps.append({"name": name, "version": version})
    return deps


def _branch_report(already_imported, modules_already_imported, new_module_names, dist_lookup):
    """This branch _report_dependencies: identical logic to main."""
    newly_imported = new_module_names - modules_already_imported
    modules_already_imported.update(new_module_names)
    if not newly_imported:
        return None
    return _branch_update(already_imported, newly_imported, dist_lookup)


# ============================================================================
# Fake distribution lookup
# ============================================================================

FAKE_DIST_MAP: dict[str, tuple[str, str]] = {}


def _seed_fake_distributions(module_names: list[str]) -> None:
    global FAKE_DIST_MAP
    FAKE_DIST_MAP = {name: (f"pkg-{name}", f"1.0.{i}") for i, name in enumerate(module_names)}


def _fake_get_dist(module_name: str):
    return FAKE_DIST_MAP.get(module_name)


# ============================================================================
# Benchmark runners
# ============================================================================


def _bench_update(impl_fn, label: str, n_modules: int, iterations: int) -> dict:
    modules = [f"bench_mod_{i}" for i in range(n_modules)]
    _seed_fake_distributions(modules)

    times = []
    peak_memories = []

    for _ in range(iterations):
        already_imported: dict = {}

        gc.collect()
        gc.disable()
        tracemalloc.start()
        t0 = time.perf_counter_ns()

        impl_fn(already_imported, modules, _fake_get_dist)

        t1 = time.perf_counter_ns()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        gc.enable()

        times.append((t1 - t0) / 1_000)
        peak_memories.append(peak)

    return {
        "impl": label,
        "time_us_mean": statistics.mean(times),
        "time_us_median": statistics.median(times),
        "time_us_p95": sorted(times)[int(len(times) * 0.95)],
        "time_us_stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "peak_mem_bytes_mean": statistics.mean(peak_memories),
        "peak_mem_bytes_max": max(peak_memories),
    }


def _bench_report(impl_fn, label: str, n_modules: int, iterations: int, entry_factory=None) -> dict:
    all_modules = [f"bench_mod_{i}" for i in range(n_modules)]
    _seed_fake_distributions(all_modules)

    split = n_modules // 2
    pre_imported_names = all_modules[:split]
    new_module_names = set(all_modules[split:])

    times = []
    peak_memories = []

    for _ in range(iterations):
        modules_already_imported = set(pre_imported_names)
        if entry_factory is not None:
            already_imported = {
                name: entry_factory(f"pkg-{name}", f"1.0.{i}") for i, name in enumerate(pre_imported_names)
            }
        else:
            already_imported = {name: f"1.0.{i}" for i, name in enumerate(pre_imported_names)}

        gc.collect()
        gc.disable()
        tracemalloc.start()
        t0 = time.perf_counter_ns()

        impl_fn(already_imported, modules_already_imported, new_module_names, _fake_get_dist)

        t1 = time.perf_counter_ns()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        gc.enable()

        times.append((t1 - t0) / 1_000)
        peak_memories.append(peak)

    return {
        "impl": label,
        "time_us_mean": statistics.mean(times),
        "time_us_median": statistics.median(times),
        "time_us_p95": sorted(times)[int(len(times) * 0.95)],
        "time_us_stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "peak_mem_bytes_mean": statistics.mean(peak_memories),
        "peak_mem_bytes_max": max(peak_memories),
    }


# ============================================================================
# Output formatting
# ============================================================================


def _compare(main_r: dict, branch_r: dict, metric: str, unit: str = "") -> str:
    mv = main_r[metric]
    bv = branch_r[metric]

    if mv == 0:
        pct_str = "N/A"
    else:
        pct = ((bv - mv) / mv) * 100
        sign = "+" if pct > 0 else ""
        pct_str = f"{sign}{pct:.1f}%"

    return f"  {metric:25s}  main={mv:>10.1f}{unit}  branch={bv:>10.1f}{unit}  ({pct_str})"


# ============================================================================
# Main
# ============================================================================


def main():
    branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()

    print(f"{'=' * 80}")
    print("  Telemetry Dependency Benchmark — main vs this branch")
    print(f"  Branch: {branch}  Commit: {commit}")
    print(f"  Python: {sys.version}")
    print(f"{'=' * 80}")

    iterations = 1500
    metrics = ["time_us_median", "time_us_p95", "peak_mem_bytes_mean"]

    # --- update_imported_dependencies ---
    for n in [50, 200, 500, 1000]:
        print(f"\n{'─' * 80}")
        print(f"  update_imported_dependencies  (n_modules={n}, iters={iterations})")
        print(f"{'─' * 80}")

        r_main = _bench_update(_main_update, "main", n, iterations)
        r_branch = _bench_update(_branch_update, "branch", n, iterations)

        for m in metrics:
            print(_compare(r_main, r_branch, m, " us" if "time" in m else " B"))

    # --- _report_dependencies ---
    for n in [50, 200, 500, 1000]:
        print(f"\n{'─' * 80}")
        print(f"  _report_dependencies  (n_modules={n}, iters={iterations})")
        print(f"{'─' * 80}")

        r_main = _bench_report(_main_report, "main", n, iterations)
        r_branch = _bench_report(
            _branch_report,
            "branch",
            n,
            iterations,
            entry_factory=lambda name, ver: DependencyEntry(name=name, version=ver),
        )

        for m in metrics:
            print(_compare(r_main, r_branch, m, " us" if "time" in m else " B"))

    print(f"\n{'=' * 80}")
    print("Done.")


if __name__ == "__main__":
    main()
