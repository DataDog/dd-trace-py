#!/usr/bin/env python3
"""
Reproduction script for lock profiler bug.

Run this with and without the fix to compare in the Datadog UI.

Usage:
    # Set your API key
    export DD_API_KEY=<your-api-key>
    
    # Run with a descriptive service name
    DD_SERVICE=lockprof-repro-BEFORE python repro_lock_profiler.py
    # Apply fix, then:
    DD_SERVICE=lockprof-repro-AFTER python repro_lock_profiler.py

Then compare in the UI:
    https://app.datadoghq.com/profiling/explorer?query=service:lockprof-repro-BEFORE
    https://app.datadoghq.com/profiling/explorer?query=service:lockprof-repro-AFTER
"""

import os
import threading
import time

# Ensure profiling is enabled
os.environ.setdefault("DD_PROFILING_ENABLED", "true")
os.environ.setdefault("DD_ENV", "repro")
os.environ.setdefault("DD_VERSION", "1.0.0")
os.environ.setdefault("DD_TRACE_AGENT_PORT", "8136")  # Local agent port

import ddtrace.auto  # noqa: E402, F401 - enables profiling

def worker(lock: threading.Lock, iterations: int, worker_id: int) -> None:
    """Simulate a worker that acquires and releases a lock."""
    for i in range(iterations):
        lock.acquire()
        # Simulate some work while holding the lock
        time.sleep(0.001)  # 1ms
        lock.release()
        
        # Some work outside the lock
        time.sleep(0.005)  # 5ms
        
        if i % 100 == 0:
            print(f"Worker {worker_id}: {i}/{iterations} iterations")


def main() -> None:
    service_name: str = os.environ.get("DD_SERVICE", "lockprof-repro")
    print(f"Starting lock profiler repro with service: {service_name}")
    print(f"Profile upload interval: ~60s")
    print(f"Run for at least 2-3 minutes to generate multiple profiles")
    print()
    
    # Create a shared lock
    shared_lock: threading.Lock = threading.Lock()
    
    # Create multiple worker threads
    num_workers: int = 4
    iterations_per_worker: int = 500
    
    threads: list[threading.Thread] = []
    for i in range(num_workers):
        t = threading.Thread(target=worker, args=(shared_lock, iterations_per_worker, i))
        threads.append(t)
    
    print(f"Starting {num_workers} workers, {iterations_per_worker} iterations each...")
    start_time: float = time.time()
    
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
    
    elapsed: float = time.time() - start_time
    total_lock_ops: int = num_workers * iterations_per_worker
    
    print()
    print(f"Completed {total_lock_ops} lock operations in {elapsed:.1f}s")
    print(f"Waiting 70s for final profile upload...")
    
    # Wait for the profiler to upload the final profile
    time.sleep(70)
    
    print()
    print(f"Done! Check the UI:")
    print(f"  https://app.datadoghq.com/profiling/explorer?query=service:{service_name}")


if __name__ == "__main__":
    main()


