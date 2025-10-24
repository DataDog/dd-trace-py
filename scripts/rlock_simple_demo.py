#!/usr/bin/env python3
"""
Simple RLock Profiling Demo

A minimal demonstration of RLock profiling with clear, easy-to-understand patterns.
Perfect for quick verification that RLock profiling is working.
"""
import os

# Configure profiler
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-simple-demo"
os.environ["DD_ENV"] = "dev"
os.environ["DD_VERSION"] = "1.0.0"
os.environ["DD_PROFILING_CAPTURE_PCT"] = "100"

# Agentless mode by default (simpler for testing)
# Just set DD_API_KEY and DD_SITE before running:
#   export DD_API_KEY="your-key"
#   export DD_SITE="datadoghq.com"  # or datadoghq.eu, etc.
#
# To use local agent instead, set DD_AGENT_HOST before running:
#   export DD_AGENT_HOST="localhost"
#   export DD_TRACE_AGENT_PORT="8126"  # or your custom port (e.g., 8136)

# Start profiler
import ddtrace.profiling.auto  # noqa: E402

import threading
import time


# Shared RLock and counter
counter_lock = threading.RLock()
counter = 0


def simple_increment(worker_id: int, iterations: int):
    """Simple counter increment showing basic RLock usage"""
    global counter
    for i in range(iterations):
        with counter_lock:
            counter += 1
            time.sleep(0.001)  # Hold lock for 1ms
        time.sleep(0.001)  # Wait 1ms between acquisitions
    print(f"Worker {worker_id} completed")


def reentrant_example(worker_id: int, iterations: int):
    """Demonstrates RLock reentrancy - key difference from Lock"""
    global counter
    for i in range(iterations):
        with counter_lock:
            # First acquisition
            counter += 1
            # Reentrant acquisition (same thread, same lock)
            with counter_lock:
                counter += 1
                time.sleep(0.001)
    print(f"Worker {worker_id} (reentrant) completed")


def main():
    print("="*60)
    print("Simple RLock Profiling Demo")
    print("="*60)
    print(f"Service: {os.environ['DD_SERVICE']}")
    print(f"Environment: {os.environ['DD_ENV']}")
    print("\nRunning 30 second test...")
    print("Watch for lock samples in Datadog APM UI")
    print("="*60 + "\n")

    threads = []

    # Start some threads with simple increment
    for i in range(5):
        t = threading.Thread(target=simple_increment, args=(i, 100))
        threads.append(t)
        t.start()

    # Start some threads with reentrant pattern
    for i in range(5, 10):
        t = threading.Thread(target=reentrant_example, args=(i, 50))
        threads.append(t)
        t.start()

    # Wait for completion
    for t in threads:
        t.join()

    print(f"\n✅ All threads completed")
    print(f"Final counter value: {counter}")
    print(f"Expected value: ~1000")

    # Wait for profile upload
    print("\nWaiting 10 seconds for profile upload...")
    time.sleep(10)

    print("\n" + "="*60)
    print("Demo completed!")
    print("Check Datadog profiling UI for lock samples at:")
    print("https://app.datadoghq.com/profiling")
    print("="*60)


if __name__ == "__main__":
    main()

