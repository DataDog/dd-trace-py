#!/usr/bin/env python
"""
Demo script for Semaphore/BoundedSemaphore lock profiling.

Run with:
  DD_SERVICE=semaphore-demo DD_ENV=dev DD_PROFILING_ENABLED=true ddtrace-run python demo_semaphore_profiling.py

Or with all options:
  DD_SERVICE=semaphore-demo \
  DD_ENV=dev \
  DD_VERSION=1.0.0 \
  DD_SITE=datadoghq.com \
  DD_TRACE_AGENT_URL=http://localhost:8136 \
  DD_PROFILING_ENABLED=true \
  DD_PROFILING_LOCK_ENABLED=true \
  ddtrace-run python demo_semaphore_profiling.py
"""

import threading
import time
import random

# =============================================================================
# USER CODE - Semaphores defined at module level
# =============================================================================

connection_pool_semaphore = threading.Semaphore(3)  # Limit to 3 concurrent DB connections
rate_limiter_bounded = threading.BoundedSemaphore(5)  # Limit to 5 concurrent API calls


def simulate_database_connection():
    """Simulates acquiring a DB connection from a limited pool."""
    with connection_pool_semaphore:
        time.sleep(random.uniform(0.05, 0.15))


def simulate_rate_limited_api_call():
    """Simulates a rate-limited external API call."""
    with rate_limiter_bounded:
        time.sleep(random.uniform(0.02, 0.08))


def worker_db_heavy(worker_id: int, iterations: int):
    """Worker thread that creates contention on the connection pool semaphore."""
    for i in range(iterations):
        simulate_database_connection()
        if i % 10 == 0:
            print(f"[DBWorker-{worker_id}] completed iteration {i}")


def worker_api_heavy(worker_id: int, iterations: int):
    """Worker thread that creates contention on the rate limiter bounded semaphore."""
    for i in range(iterations):
        simulate_rate_limited_api_call()
        if i % 10 == 0:
            print(f"[APIWorker-{worker_id}] completed iteration {i}")


def main():
    print("=" * 70)
    print("SEMAPHORE / BOUNDEDSEMAPHORE PROFILING DEMO")
    print("=" * 70)
    print()
    print("Expected in Datadog UI (APM -> Profiler -> Lock Wait/Hold Time):")
    print("  - Lock names: connection_pool_semaphore, rate_limiter_bounded")
    print("  - Stack traces: simulate_database_connection, simulate_rate_limited_api_call")
    print()
    
    threads = []
    num_db_workers = 8
    num_api_workers = 10
    iterations = 50
    
    for i in range(num_db_workers):
        t = threading.Thread(target=worker_db_heavy, args=(i, iterations), name=f"DBWorker-{i}")
        threads.append(t)
    
    for i in range(num_api_workers):
        t = threading.Thread(target=worker_api_heavy, args=(i, iterations), name=f"APIWorker-{i}")
        threads.append(t)
    
    print(f"Starting {len(threads)} workers...")
    start = time.time()
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(f"\nDone in {time.time() - start:.2f}s")
    print("Waiting 5s for profiler to flush...")
    time.sleep(5)


if __name__ == "__main__":
    main()
