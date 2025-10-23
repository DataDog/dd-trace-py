#!/usr/bin/env python3
"""
Heavy RLock Contention Demo

This creates VERY obvious lock contention that should be immediately visible in profiling UI.
Use this for demos and presentations where you want dramatic, clear lock samples.
"""
import os
import threading
import time
from dataclasses import dataclass
from typing import List

# Configure profiler for maximum visibility
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-heavy-contention"
os.environ["DD_ENV"] = "demo"
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


@dataclass
class ContendedResource:
    """A heavily contended resource protected by RLock"""

    lock: threading.RLock
    access_count: int = 0
    write_count: int = 0
    data: List[str] = None

    def __post_init__(self):
        if self.data is None:
            self.data = []


# Global heavily contended resource
BOTTLENECK = ContendedResource(lock=threading.RLock())


def heavy_writer(worker_id: int, duration_seconds: int):
    """
    Writer thread that holds lock for long periods.
    This creates obvious contention and wait times.
    """
    end_time = time.time() + duration_seconds
    iteration = 0

    while time.time() < end_time:
        with BOTTLENECK.lock:
            # Long critical section
            BOTTLENECK.write_count += 1
            BOTTLENECK.data.append(f"writer-{worker_id}-{iteration}")

            # Simulate expensive operation while holding lock
            time.sleep(0.01)  # 10ms - intentionally long to create contention

            # Keep data size bounded
            if len(BOTTLENECK.data) > 1000:
                BOTTLENECK.data = BOTTLENECK.data[-500:]

        iteration += 1
        time.sleep(0.001)  # Small gap between lock acquisitions

    print(f"Writer {worker_id} finished ({iteration} iterations)")


def aggressive_reader(worker_id: int, duration_seconds: int):
    """
    Reader thread that tries to access resource very frequently.
    Will show high wait times due to writers holding lock.
    """
    end_time = time.time() + duration_seconds
    iteration = 0

    while time.time() < end_time:
        with BOTTLENECK.lock:
            BOTTLENECK.access_count += 1
            # Quick read
            _ = len(BOTTLENECK.data)
            time.sleep(0.001)  # 1ms hold time

        iteration += 1
        # No sleep - immediately try to acquire again (aggressive!)

    print(f"Reader {worker_id} finished ({iteration} iterations)")


def reentrant_nested_worker(worker_id: int, duration_seconds: int):
    """
    Demonstrates deep reentrancy with obvious patterns.
    This shows RLock-specific behavior clearly.
    """
    end_time = time.time() + duration_seconds
    iteration = 0

    while time.time() < end_time:
        # Level 1
        with BOTTLENECK.lock:
            BOTTLENECK.access_count += 1

            # Level 2 (reentrant)
            with BOTTLENECK.lock:
                BOTTLENECK.write_count += 1

                # Level 3 (reentrant)
                with BOTTLENECK.lock:
                    BOTTLENECK.data.append(f"nested-{worker_id}-{iteration}")
                    time.sleep(0.005)  # 5ms at deepest level

        iteration += 1
        time.sleep(0.002)

    print(f"Nested worker {worker_id} finished ({iteration} iterations)")


def print_stats():
    """Print statistics about the contended resource"""
    with BOTTLENECK.lock:
        print(f"\n{'='*60}")
        print("Final Statistics:")
        print(f"  Total accesses: {BOTTLENECK.access_count}")
        print(f"  Total writes: {BOTTLENECK.write_count}")
        print(f"  Data items: {len(BOTTLENECK.data)}")
        print(f"{'='*60}")


def main():
    print("\n" + "="*60)
    print("Heavy RLock Contention Demo")
    print("="*60)
    print("\nThis creates EXTREME lock contention for obvious profiling samples.")
    print("\nConfiguration:")
    print(f"  Service: {os.environ['DD_SERVICE']}")
    print(f"  Environment: {os.environ['DD_ENV']}")
    print(f"  Version: {os.environ['DD_VERSION']}")
    print("\nThread Configuration:")
    print("  - 3 writer threads (hold lock for 10ms each)")
    print("  - 8 aggressive reader threads (try to acquire constantly)")
    print("  - 4 nested reentrant threads (deep RLock reentrancy)")
    print("\nThis should produce:")
    print("  ✓ High lock wait time samples (due to contention)")
    print("  ✓ Clear lock acquire/release patterns")
    print("  ✓ Obvious reentrant lock behavior")
    print("="*60 + "\n")

    duration = 300  # Run for 5 minutes
    threads = []

    print(f"Starting threads (will run for {duration} seconds)...\n")

    # Start writer threads (few but slow)
    for i in range(3):
        t = threading.Thread(target=heavy_writer, args=(i, duration), name=f"Writer-{i}")
        threads.append(t)
        t.start()

    # Start aggressive reader threads (many, fast attempts)
    for i in range(8):
        t = threading.Thread(target=aggressive_reader, args=(i, duration), name=f"Reader-{i}")
        threads.append(t)
        t.start()

    # Start reentrant nested threads
    for i in range(4):
        t = threading.Thread(target=reentrant_nested_worker, args=(i, duration), name=f"Nested-{i}")
        threads.append(t)
        t.start()

    print(f"All {len(threads)} threads started!\n")

    # Show progress
    for i in range(duration):
        time.sleep(1)
        if (i + 1) % 10 == 0:
            print(f"  ... {i + 1}s elapsed ...")

    # Wait for all threads
    print("\nWaiting for threads to complete...")
    for t in threads:
        t.join()

    print("\n✅ All threads completed!")

    # Print statistics
    print_stats()

    # Wait for profile upload
    print("\nWaiting 15 seconds for profile upload...")
    for i in range(15, 0, -1):
        print(f"  {i}...", end="\r")
        time.sleep(1)

    print("\n\n" + "="*60)
    print("Demo Complete!")
    print("="*60)
    print("\nView profiling data at:")
    print("  https://app.datadoghq.com/profiling")
    print(f"\nFilter by:")
    print(f"  Service: {os.environ['DD_SERVICE']}")
    print(f"  Environment: {os.environ['DD_ENV']}")
    print("\nLook for:")
    print("  • Lock wait time samples (should be very high)")
    print("  • Lock acquire/release patterns")
    print("  • Reentrant lock traces (nested acquisitions)")
    print("  • Thread contention visualization")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()

