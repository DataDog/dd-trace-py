"""Simple test application that exercises the dd-trace-py profiler.

Starts the profiler with memory + stack collection enabled, then runs
a workload to trigger profiler activity (sampling, heap tracking, uploads).
This is meant to be run under ddprof to capture the *profiler's own*
native allocations.
"""
import os
import time
import threading

# Disable upload so we don't need a real agent
os.environ.setdefault("DD_PROFILING_UPLOAD_INTERVAL", "5")
os.environ.setdefault("DD_PROFILING_ENABLED", "true")
os.environ.setdefault("DD_PROFILING_MEMORY_ENABLED", "true")
os.environ.setdefault("DD_PROFILING_STACK_ENABLED", "true")
os.environ.setdefault("DD_TRACE_AGENT_URL", "http://localhost:18126")  # bogus agent to avoid connection errors

from ddtrace.profiling import Profiler  # noqa: E402


DURATION = int(os.environ.get("TEST_DURATION", "30"))  # seconds


def cpu_work():
    """Do some CPU work to trigger stack sampling."""
    total = 0
    for i in range(500_000):
        total += i * i
    return total


def alloc_work():
    """Do some allocations to trigger heap tracking."""
    data = []
    for _ in range(10_000):
        data.append(bytearray(1024))
    return data


def worker():
    """Thread worker that alternates CPU and allocation work."""
    for _ in range(100):
        cpu_work()
        alloc_work()
        time.sleep(0.01)


def main():
    profiler = Profiler()
    profiler.start()

    print(f"Profiler started, running workload for {DURATION}s...")

    end_time = time.time() + DURATION
    threads = []

    while time.time() < end_time:
        # Spawn worker threads to exercise multi-threaded profiling
        batch = []
        for _ in range(4):
            t = threading.Thread(target=worker)
            t.start()
            batch.append(t)
        for t in batch:
            t.join()

    print("Workload complete, stopping profiler...")
    profiler.stop(flush=True)
    print("Done.")


if __name__ == "__main__":
    main()
