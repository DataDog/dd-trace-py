"""Baseline fork latency and fork storm measurements.

Usage::

    python tests/internal/fork_lifecycle_baseline.py [bare|ddtrace] [--iterations N] [--storm N]

``bare`` measures the interpreter without ddtrace. ``ddtrace`` enables telemetry, traces one span so
the native writer exists, and starts one PeriodicService before measuring. Prints one JSON object.
Numbers are informational: compare runs on the same machine, Python version and mode.

Not collected by pytest (its name does not start with "test").
"""

import argparse
import ctypes
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import warnings


warnings.simplefilter("ignore", DeprecationWarning)


def stats(samples_s):
    ms = sorted(s * 1e3 for s in samples_s)

    def pct(p):
        return round(ms[min(len(ms) - 1, int(p / 100 * len(ms)))], 4)

    return {
        "n": len(ms),
        "mean_ms": round(statistics.fmean(ms), 4),
        "p50_ms": pct(50),
        "p90_ms": pct(90),
        "p99_ms": pct(99),
    }


def fork_samples(fork, iterations):
    """Return (parent-return, child-first-instruction, round-trip) durations for each fork."""
    parent_ret, child_start, round_trip = [], [], []
    for _ in range(iterations):
        r, w = os.pipe()
        t0 = time.perf_counter_ns()
        pid = fork()
        if pid == 0:
            # perf_counter_ns is CLOCK_MONOTONIC on Linux and macOS, so it is shared with the parent.
            os.write(w, str(time.perf_counter_ns()).encode())
            os._exit(0)
        t1 = time.perf_counter_ns()
        os.close(w)
        child_t = int(os.read(r, 64))
        os.close(r)
        os.waitpid(pid, 0)
        t2 = time.perf_counter_ns()
        parent_ret.append((t1 - t0) / 1e9)
        child_start.append((child_t - t0) / 1e9)
        round_trip.append((t2 - t0) / 1e9)
    return parent_ret, child_start, round_trip


def storm(fork, total, n_threads=1):
    per_thread = total // n_threads
    samples = []
    lock = threading.Lock()

    def run():
        local = []
        for _ in range(per_thread):
            t0 = time.perf_counter()
            pid = fork()
            if pid == 0:
                os._exit(0)
            os.waitpid(pid, 0)
            local.append(time.perf_counter() - t0)
        with lock:
            samples.extend(local)

    t0 = time.perf_counter()
    workers = [threading.Thread(target=run) for _ in range(n_threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    wall = time.perf_counter() - t0
    return dict(stats(samples), wall_s=round(wall, 4), forks_per_s=round(len(samples) / wall, 1), threads=n_threads)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["bare", "ddtrace"], nargs="?", default="ddtrace")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--storm", type=int, default=500)
    args = parser.parse_args()

    svc = recover = None
    if args.mode == "ddtrace":
        from ddtrace.internal import periodic
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.trace import tracer

        class Counter(periodic.PeriodicService):
            runs = 0

            def periodic(self):
                self.runs += 1

        svc = Counter(interval=0.01)
        svc.start()
        telemetry_writer.enable()
        with tracer.trace("baseline"):
            pass
        telemetry_writer.periodic(force_flush=True)

        def recover():
            """Seconds until the parent's periodic service ran again and a native flush succeeded."""
            t0 = time.perf_counter()
            runs = svc.runs
            while svc.runs == runs and time.perf_counter() - t0 < 10:
                time.sleep(0.001)
            telemetry_writer._worker.flush()
            return round(time.perf_counter() - t0, 4)

    libc = ctypes.PyDLL(None)
    result = {
        "mode": args.mode,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
    }
    parent_ret, child_start, round_trip = fork_samples(os.fork, args.iterations)
    result["os_fork_parent_return"] = stats(parent_ret)
    result["os_fork_child_start"] = stats(child_start)
    result["os_fork_round_trip"] = stats(round_trip)
    result["libc_fork_round_trip"] = stats(fork_samples(libc.fork, args.iterations)[2])

    for name, kwargs in (("subprocess_run", {}), ("subprocess_run_preexec_fn", {"preexec_fn": lambda: None})):
        samples = []
        for _ in range(max(1, args.iterations // 4)):
            t0 = time.perf_counter()
            subprocess.run(["true"], check=True, **kwargs)
            samples.append(time.perf_counter() - t0)
        result[name] = stats(samples)

    result["storm_os_fork"] = storm(os.fork, args.storm)
    if recover:
        result["storm_os_fork"]["parent_recovery_s"] = recover()
    result["storm_os_fork_4_threads"] = storm(os.fork, min(args.storm, 200), n_threads=4)
    if recover:
        result["storm_os_fork_4_threads"]["parent_recovery_s"] = recover()

    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
