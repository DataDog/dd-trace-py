#!/usr/bin/env python3
"""Jitter probe: how much time each probe CPU steals from a tight user loop.

EXPERIMENT (do not merge): T6 of the CPU-asymmetry probe on benchmarking
hosts (PR #20052 / APMSP-4059). Before the benchmark starts (the CI job runs
this from its preflight, strictly before run-benchmarks.sh), one process per
probe CPU is pinned with os.sched_setaffinity and spins on
time.perf_counter_ns for ~20 s. Any timestamp gap at or above a calibrated
threshold (default floor 5 us) is time the CPU was taken away from the loop:
interrupt handling, SMIs, other tasks, or migrations. Recorded per CPU: gap
count, total stolen time, a size histogram, an inter-gap interval histogram
(periodicity -- the H7-SMI/kthreads signature is a few long, evenly spaced
gaps) and the raw events. Processes, not threads, so each probe has its own
GIL and the probes never jitter each other.

Stdlib only; every per-CPU failure is recorded as {available, error} and the
probe never raises or fails the job.

Usage: jitter.py --out FILE [--cpus 24,25,36,37] [--seconds 20]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import sys
import time


# gap-size histogram edges (ns); the last bucket is unbounded
SIZE_EDGES_NS = (5_000, 10_000, 50_000, 100_000, 500_000, 2_000_000)
# inter-gap interval histogram edges (s) for the periodicity readout
INTERVAL_EDGES_S = (0.001, 0.01, 0.1, 1.0)


def calibrate_threshold(spacings: list[int], floor_ns: int, multiple: int = 10) -> int:
    """Gap threshold from the loop's own iteration spacings: the floor (5 us
    by default) or 10x the median spacing, whichever is larger, so a slow
    host cannot turn ordinary iterations into 'gaps'.
    """
    median = statistics.median(spacings) if spacings else 0
    return max(floor_ns, int(median) * multiple)


def histogram(values: list, edges) -> dict:
    """Bucket counts over ascending edges; the last bucket is unbounded and
    keys are "lo-hi" strings (in the values' own unit).
    """
    counts: dict[str, int] = {}
    bounds = list(edges) + [float("inf")]
    lo = 0
    for hi in bounds:
        counts["%s-%s" % (lo, hi if hi != float("inf") else "inf")] = 0
        lo = hi
    for value in values:
        lo = 0
        for hi in bounds:
            if value < hi:
                counts["%s-%s" % (lo, hi if hi != float("inf") else "inf")] += 1
                break
            lo = hi
    return counts


def summarize(gaps: list, threshold_ns: int, median_iter_ns: int, duration_s: float) -> dict:
    """Per-CPU summary from the recorded (t_offset_ns, gap_ns) events: count,
    stolen time (gap minus one baseline iteration), size histogram, and the
    inter-gap interval histogram the periodicity judgement reads.
    """
    total_gap_ns = sum(gap for _, gap in gaps)
    intervals = [(gaps[i][0] - gaps[i - 1][0]) / 1e9 for i in range(1, len(gaps)) if gaps[i][0] > gaps[i - 1][0]]
    return {
        "threshold_ns": threshold_ns,
        "median_iter_ns": median_iter_ns,
        "duration_s": duration_s,
        "gap_count": len(gaps),
        "total_gap_ns": total_gap_ns,
        "total_stolen_ns": max(0, total_gap_ns - len(gaps) * median_iter_ns),
        "stolen_fraction": (total_gap_ns / (duration_s * 1e9)) if duration_s > 0 else None,
        "size_histogram_ns": histogram([gap for _, gap in gaps], SIZE_EDGES_NS),
        "interval_histogram_s": histogram(intervals, INTERVAL_EDGES_S),
        "max_gap_ns": max((gap for _, gap in gaps), default=0),
    }


def probe_cpu(cpu: int, seconds: float, calibrate_s: float, floor_ns: int, max_events: int) -> dict:
    """One pinned probe process. Returns the per-CPU record; failures land in
    {available, error} and never raise out of here.
    """
    record: dict = {"cpu": cpu, "available": True, "error": None}
    try:
        os.sched_setaffinity(0, {cpu})
    except (AttributeError, OSError) as exc:
        return {"cpu": cpu, "available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        # calibration: measure the loop's ordinary iteration spacing first so
        # the gap threshold cannot sit inside normal noise on a slow host
        spacings = []
        end = time.perf_counter() + calibrate_s
        prev = time.perf_counter_ns()
        while time.perf_counter() < end:
            now = time.perf_counter_ns()
            spacings.append(now - prev)
            prev = now
        threshold = calibrate_threshold(spacings, floor_ns)
        median_iter = int(statistics.median(spacings)) if spacings else 0

        gaps = []
        events = []
        start = time.perf_counter()
        start_ns = time.perf_counter_ns()
        prev = start_ns
        end = start + seconds
        while True:
            now = time.perf_counter_ns()
            if now - prev >= threshold:
                gaps.append((now - start_ns, now - prev))
                if len(events) < max_events:
                    events.append([now - start_ns, now - prev])
            prev = now
            if now - start_ns >= seconds * 1e9:
                break
        duration = (time.perf_counter_ns() - start_ns) / 1e9
        record["calibration_samples"] = len(spacings)
        record["result"] = summarize(gaps, threshold, median_iter, duration)
        record["events"] = events
        if len(gaps) > max_events:
            record["events_truncated"] = True
        return record
    except Exception as exc:  # never fail the job on a probe
        return {"cpu": cpu, "available": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def run_probe(cpus: list, seconds: float, calibrate_s: float, floor_ns: int, max_events: int) -> dict:
    """Run one pinned probe process per CPU in parallel and join the results.
    Multiprocessing (not threads) so each probe has its own GIL and never
    jitters another probe CPU.
    """
    out = {
        "t_start": time.time(),
        "duration_s": seconds + calibrate_s,
        "min_threshold_ns": floor_ns,
        "cpus": [int(cpu) for cpu in cpus],
        "results": [],
    }
    if not cpus:
        return out
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(processes=len(cpus)) as pool:
        promises = [pool.apply_async(probe_cpu, (cpu, seconds, calibrate_s, floor_ns, max_events)) for cpu in cpus]
        for promise in promises:
            try:
                out["results"].append(promise.get(timeout=seconds + calibrate_s + 120))
            except Exception as exc:  # a dead probe is recorded, not fatal
                out["results"].append({"cpu": None, "available": False, "error": str(exc)})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="output JSON file")
    parser.add_argument("--cpus", default="24,25,36,37", help="comma-separated probe CPUs")
    parser.add_argument("--seconds", type=float, default=20.0, help="measurement window per CPU")
    parser.add_argument("--calibrate-seconds", type=float, default=1.0, help="threshold calibration window")
    parser.add_argument("--min-threshold-ns", type=int, default=5000, help="gap threshold floor")
    parser.add_argument("--max-events", type=int, default=20000, help="raw events kept per CPU")
    args = parser.parse_args()
    cpus = [int(cpu) for cpu in args.cpus.split(",") if cpu.strip()]
    report = run_probe(cpus, args.seconds, args.calibrate_seconds, args.min_threshold_ns, args.max_events)
    try:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    except OSError as exc:
        print("jitter probe: could not write %s: %s" % (args.out, exc), file=sys.stderr)
    print("jitter probe (T6): %s" % json.dumps({k: v for k, v in report.items() if k != "results"}))
    for result in report["results"]:
        summary = result.get("result") or {}
        print(
            "  cpu=%s available=%s gaps=%s stolen=%s%%"
            % (
                result.get("cpu"),
                result.get("available"),
                summary.get("gap_count"),
                round((summary.get("stolen_fraction") or 0) * 100, 3),
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
