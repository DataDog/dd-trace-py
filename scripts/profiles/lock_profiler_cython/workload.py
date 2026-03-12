"""Cython A/B benchmark workload for the lock profiler.

Runs under ddtrace-run with profiling + lock profiling enabled.
Exercises lock acquire/release in a tight loop to measure profiler overhead.

Outputs structured JSON to stdout with timing results.
Diagnostic messages go to stderr.
"""

import json
import os
import sys
import threading
import time


N_OPS = int(os.environ.get("LOCKBENCH_OPS", "50000"))
N_THREADS = int(os.environ.get("LOCKBENCH_THREADS", "4"))


def run_uncontended(n: int) -> dict:
    """Single-thread tight acquire/release loop."""
    lock = threading.Lock()
    start = time.monotonic_ns()
    for _ in range(n):
        lock.acquire()
        lock.release()
    elapsed_ns = time.monotonic_ns() - start
    return {
        "scenario": "uncontended",
        "ops": n,
        "threads": 1,
        "elapsed_ns": elapsed_ns,
        "ops_per_sec": n / (elapsed_ns / 1e9) if elapsed_ns > 0 else 0,
    }


def _contended_worker(lock: threading.Lock, n: int) -> None:
    for _ in range(n):
        lock.acquire()
        lock.release()


def run_contended(n: int, n_threads: int) -> dict:
    """Multiple threads competing on a single lock."""
    lock = threading.Lock()
    ops_per_thread = n // n_threads
    threads = [threading.Thread(target=_contended_worker, args=(lock, ops_per_thread)) for _ in range(n_threads)]

    start = time.monotonic_ns()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed_ns = time.monotonic_ns() - start

    total_ops = ops_per_thread * n_threads
    return {
        "scenario": "contended",
        "ops": total_ops,
        "threads": n_threads,
        "elapsed_ns": elapsed_ns,
        "ops_per_sec": total_ops / (elapsed_ns / 1e9) if elapsed_ns > 0 else 0,
    }


def main() -> None:
    lock_type = f"{type(threading.Lock()).__module__}.{type(threading.Lock()).__qualname__}"
    print(f"Lock type: {lock_type}", file=sys.stderr)
    print(f"Ops: {N_OPS}, Threads: {N_THREADS}", file=sys.stderr)

    results = {
        "pid": os.getpid(),
        "lock_type": lock_type,
        "uncontended": run_uncontended(N_OPS),
        "contended": run_contended(N_OPS, N_THREADS),
    }

    # Let the profiler scheduler flush at least one profile
    time.sleep(3)

    json.dump(results, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
