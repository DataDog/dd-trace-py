"""Analyze A/B benchmark results: main (pure Python) vs Cython lock profiler.

Usage:
    python analyze.py <results_dir>

Where <results_dir> contains pct_N/ subdirectories (one per capture rate),
each with main/ and cython/ subdirectories containing:
  - run_N.json       (timing output from workload.py)
  - pprof_N.*.pprof  (profiler output)
"""

import glob
import json
import os
import statistics
import sys

import zstandard as zstd

from tests.profiling.collector.pprof_utils import get_samples_with_value_type
from tests.profiling.collector.pprof_utils import pprof_pb2


def load_timing_results(results_dir: str) -> list[dict]:
    """Load all run_*.json files from a results directory."""
    files = sorted(glob.glob(os.path.join(results_dir, "run_*.json")))
    results = []
    for f in files:
        with open(f) as fp:
            results.append(json.load(fp))
    return results


def parse_pprof_files(results_dir: str) -> list[tuple[int, int]]:
    """Parse all pprof files and return (acquire_count, release_count) per run."""
    counts = []
    for i in range(1, 100):
        pattern = os.path.join(results_dir, f"pprof_{i}.*.pprof")
        files = sorted(glob.glob(pattern))
        if not files:
            break

        acq_total = 0
        rel_total = 0
        for filename in files:
            if not filename.endswith(".pprof"):
                continue
            with open(filename, "rb") as fp:
                data = zstd.ZstdDecompressor().stream_reader(fp).read()
            profile = pprof_pb2.Profile()
            profile.ParseFromString(data)
            try:
                acq_total += len(get_samples_with_value_type(profile, "lock-acquire"))
            except StopIteration:
                pass
            try:
                rel_total += len(get_samples_with_value_type(profile, "lock-release"))
            except StopIteration:
                pass
        counts.append((acq_total, rel_total))
    return counts


def summarize_timing(results: list[dict], scenario: str) -> dict:
    """Extract median ops/sec and elapsed_ns for a scenario across runs."""
    ops_per_sec = [r[scenario]["ops_per_sec"] for r in results]
    elapsed = [r[scenario]["elapsed_ns"] for r in results]
    return {
        "median_ops_per_sec": statistics.median(ops_per_sec),
        "median_elapsed_ms": statistics.median(elapsed) / 1e6,
        "stdev_ops_per_sec": statistics.stdev(ops_per_sec) if len(ops_per_sec) > 1 else 0,
    }


def analyze_one_rate(results_dir: str, capture_pct: int) -> bool:
    """Analyze results for a single capture rate. Returns True if all checks pass."""
    main_dir = os.path.join(results_dir, f"pct_{capture_pct}", "main")
    cython_dir = os.path.join(results_dir, f"pct_{capture_pct}", "cython")

    main_results = load_timing_results(main_dir)
    cython_results = load_timing_results(cython_dir)

    if not main_results or not cython_results:
        print(f"  SKIP: No results for capture_pct={capture_pct}%")
        return True

    print(f"\n  CAPTURE RATE: {capture_pct}%")
    print(f"  {'=' * 68}")

    for scenario in ("uncontended", "contended"):
        main_s = summarize_timing(main_results, scenario)
        cython_s = summarize_timing(cython_results, scenario)

        main_ops = main_s["median_ops_per_sec"]
        cython_ops = cython_s["median_ops_per_sec"]
        delta_pct = ((cython_ops - main_ops) / main_ops * 100) if main_ops > 0 else 0

        main_ms = main_s["median_elapsed_ms"]
        cython_ms = cython_s["median_elapsed_ms"]
        time_delta_pct = ((cython_ms - main_ms) / main_ms * 100) if main_ms > 0 else 0

        print(f"\n    {scenario.upper()}")
        print(f"    {'':28s} {'main':>14s}   {'cython':>14s}   {'delta':>10s}")
        print(f"    {'':28s} {'----':>14s}   {'------':>14s}   {'-----':>10s}")
        print(f"    {'median ops/sec':28s} {main_ops:>14,.0f}   {cython_ops:>14,.0f}   {delta_pct:>+9.1f}%")
        print(f"    {'median elapsed (ms)':28s} {main_ms:>14.1f}   {cython_ms:>14.1f}   {time_delta_pct:>+9.1f}%")
        print(
            f"    {'stdev ops/sec':28s} {main_s['stdev_ops_per_sec']:>14,.0f}   {cython_s['stdev_ops_per_sec']:>14,.0f}"
        )

    # Lock samples
    main_counts = parse_pprof_files(main_dir)
    cython_counts = parse_pprof_files(cython_dir)

    if main_counts and cython_counts:
        m_acq = statistics.median([c[0] for c in main_counts])
        c_acq = statistics.median([c[0] for c in cython_counts])
        m_rel = statistics.median([c[1] for c in main_counts])
        c_rel = statistics.median([c[1] for c in cython_counts])

        print("\n    LOCK SAMPLES")
        print(f"    {'':28s} {'main':>14s}   {'cython':>14s}")
        print(f"    {'':28s} {'----':>14s}   {'------':>14s}")
        print(f"    {'median lock-acquire':28s} {m_acq:>14.0f}   {c_acq:>14.0f}")
        print(f"    {'median lock-release':28s} {m_rel:>14.0f}   {c_rel:>14.0f}")

    # Verdict for this rate
    all_pass = True
    main_acq_total = sum(c[0] for c in main_counts) if main_counts else 0
    cython_acq_total = sum(c[0] for c in cython_counts) if cython_counts else 0

    if main_acq_total > 0 and cython_acq_total > 0:
        ratio = cython_acq_total / main_acq_total
        if ratio < 0.5 or ratio > 2.0:
            print(f"    WARN: Lock sample counts differ significantly (ratio: {ratio:.2f}x)")
        else:
            print(f"    PASS: Lock sample counts are similar (ratio: {ratio:.2f}x)")
    elif cython_acq_total == 0 and capture_pct > 0:
        print(f"    FAIL: Cython branch has 0 lock-acquire samples at {capture_pct}% capture")
        all_pass = False

    for scenario in ("uncontended", "contended"):
        main_ops = statistics.median([r[scenario]["ops_per_sec"] for r in main_results])
        cython_ops = statistics.median([r[scenario]["ops_per_sec"] for r in cython_results])
        delta = (cython_ops - main_ops) / main_ops * 100 if main_ops > 0 else 0
        if delta > 0:
            print(f"    PASS: {scenario} — Cython is {delta:+.1f}% faster")
        else:
            print(f"    INFO: {scenario} — Cython is {delta:+.1f}% (no speedup)")

    return all_pass


def print_summary_table(results_dir: str, capture_pcts: list[int]) -> None:
    """Print a compact cross-rate comparison table."""
    print(f"\n  {'=' * 68}")
    print("  SUMMARY: Speedup by capture rate")
    print(f"  {'=' * 68}")
    print(f"  {'capture %':>10s}   {'uncontended':>14s}   {'contended':>14s}")
    print(f"  {'':>10s}   {'(ops/sec delta)':>14s}   {'(ops/sec delta)':>14s}")
    print(f"  {'-' * 10:>10s}   {'-' * 14:>14s}   {'-' * 14:>14s}")

    for pct in capture_pcts:
        main_dir = os.path.join(results_dir, f"pct_{pct}", "main")
        cython_dir = os.path.join(results_dir, f"pct_{pct}", "cython")
        main_results = load_timing_results(main_dir)
        cython_results = load_timing_results(cython_dir)
        if not main_results or not cython_results:
            continue

        deltas = []
        for scenario in ("uncontended", "contended"):
            main_ops = statistics.median([r[scenario]["ops_per_sec"] for r in main_results])
            cython_ops = statistics.median([r[scenario]["ops_per_sec"] for r in cython_results])
            delta = (cython_ops - main_ops) / main_ops * 100 if main_ops > 0 else 0
            deltas.append(delta)

        print(f"  {pct:>9d}%   {deltas[0]:>+13.1f}%   {deltas[1]:>+13.1f}%")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <results_dir>", file=sys.stderr)
        return 2

    results_dir = sys.argv[1]

    # Discover capture rates from directory structure
    pct_dirs = sorted(glob.glob(os.path.join(results_dir, "pct_*")))
    if not pct_dirs:
        # Legacy: flat structure without pct_ prefix
        print("ERROR: No pct_* directories found", file=sys.stderr)
        return 1

    capture_pcts = []
    for d in pct_dirs:
        try:
            pct = int(os.path.basename(d).replace("pct_", ""))
            capture_pcts.append(pct)
        except ValueError:
            continue

    print("\n" + "=" * 72)
    print("  LOCK PROFILER CYTHON A/B BENCHMARK")
    print("=" * 72)

    all_pass = True
    for pct in capture_pcts:
        if not analyze_one_rate(results_dir, pct):
            all_pass = False

    print_summary_table(results_dir, capture_pcts)

    print("\n" + "-" * 72)
    if all_pass:
        print("  ALL CHECKS PASSED")
    else:
        print("  SOME CHECKS FAILED")
    print("-" * 72)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
