#!/usr/bin/env python3
"""
RLock Profiling Stress Test

This script demonstrates RLock profiling with clear acquire, release, and wait time samples.
It uses the Datadog profiler API directly (not ddtrace-run).
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Set up profiler environment before importing ddtrace
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-stress-test"
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

# Import and configure the profiler
import ddtrace.profiling.auto  # noqa: E402

print("Datadog Profiler started")
print(f"Service: {os.environ['DD_SERVICE']}")
print(f"Environment: {os.environ['DD_ENV']}")
print("-" * 80)


class WorkloadGenerator:
    """Generate different lock contention patterns"""

    def __init__(self):
        # Different RLocks for different scenarios
        self.shared_resource_lock = threading.RLock()
        self.counter_lock = threading.RLock()
        self.cache_lock = threading.RLock()
        self.nested_lock = threading.RLock()

        # Shared state
        self.counter = 0
        self.cache = {}
        self.shared_resource = []

    def high_contention_workload(self, worker_id: int, iterations: int):
        """
        High contention scenario: multiple threads competing for the same lock.
        This should show good wait time samples.
        """
        for i in range(iterations):
            with self.shared_resource_lock:
                # Simulate some work while holding the lock
                self.shared_resource.append(f"worker-{worker_id}-item-{i}")
                time.sleep(0.001)  # 1ms hold time to create contention
                if len(self.shared_resource) > 100:
                    self.shared_resource.pop(0)

    def counter_increment_workload(self, iterations: int):
        """
        Counter increment pattern: short critical sections with high frequency.
        This should show many acquire/release samples.
        """
        for _ in range(iterations):
            with self.counter_lock:
                self.counter += 1
            # Small delay between lock acquisitions
            time.sleep(0.0001)

    def reentrant_lock_workload(self, worker_id: int, iterations: int):
        """
        Demonstrates RLock reentrancy: same thread acquires lock multiple times.
        This is the key feature of RLock that differentiates it from Lock.
        """
        for i in range(iterations):
            with self.nested_lock:
                # First level acquisition
                self._nested_operation_level1(worker_id, i)

    def _nested_operation_level1(self, worker_id: int, item: int):
        """Helper method that re-acquires the same RLock"""
        with self.nested_lock:  # Reentrant acquisition
            self._nested_operation_level2(worker_id, item)

    def _nested_operation_level2(self, worker_id: int, item: int):
        """Helper method that re-acquires the same RLock again"""
        with self.nested_lock:  # Another reentrant acquisition
            # Do some work
            time.sleep(0.0005)
            self.cache[f"worker-{worker_id}-{item}"] = time.time()

    def cache_access_workload(self, worker_id: int, iterations: int):
        """
        Read-heavy workload with occasional writes.
        Shows realistic lock usage patterns.
        """
        for i in range(iterations):
            # Read from cache (still needs lock in this example)
            with self.cache_lock:
                key = f"worker-{worker_id}-{i % 10}"
                value = self.cache.get(key)

            # Occasionally write
            if i % 5 == 0:
                with self.cache_lock:
                    key = f"worker-{worker_id}-{i}"
                    self.cache[key] = {"timestamp": time.time(), "worker": worker_id}
                    time.sleep(0.0002)

            time.sleep(0.0001)

    def mixed_workload(self, worker_id: int, iterations: int):
        """
        Mixed workload combining different patterns.
        Simulates realistic application behavior.
        """
        for i in range(iterations):
            # Alternate between different operations
            if i % 3 == 0:
                self.high_contention_workload(worker_id, 5)
            elif i % 3 == 1:
                self.counter_increment_workload(10)
            else:
                self.reentrant_lock_workload(worker_id, 3)


def run_stress_test(duration_seconds: int = 60, num_workers: int = 10):
    """
    Run comprehensive RLock stress test.

    Args:
        duration_seconds: How long to run the test
        num_workers: Number of concurrent worker threads
    """
    print(f"\n{'='*80}")
    print(f"Starting RLock Stress Test")
    print(f"Duration: {duration_seconds} seconds")
    print(f"Workers: {num_workers}")
    print(f"{'='*80}\n")

    workload = WorkloadGenerator()
    start_time = time.time()
    iteration_count = 0

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        # Phase 1: High Contention (first 1/3 of time)
        phase_duration = duration_seconds / 3
        print(f"Phase 1: High Contention Workload ({phase_duration:.0f}s)")
        phase_start = time.time()
        futures = []

        while time.time() - phase_start < phase_duration:
            futures = [
                executor.submit(workload.high_contention_workload, worker_id, 50)
                for worker_id in range(num_workers)
            ]
            for future in futures:
                future.result()
            iteration_count += 1

        print(f"  ✓ Completed {iteration_count} iterations")

        # Phase 2: Reentrant Lock Pattern (second 1/3 of time)
        iteration_count = 0
        print(f"\nPhase 2: Reentrant Lock Workload ({phase_duration:.0f}s)")
        phase_start = time.time()

        while time.time() - phase_start < phase_duration:
            futures = [
                executor.submit(workload.reentrant_lock_workload, worker_id, 20)
                for worker_id in range(num_workers)
            ]
            for future in futures:
                future.result()
            iteration_count += 1

        print(f"  ✓ Completed {iteration_count} iterations")

        # Phase 3: Mixed Workload (final 1/3 of time)
        iteration_count = 0
        print(f"\nPhase 3: Mixed Workload ({phase_duration:.0f}s)")
        phase_start = time.time()

        while time.time() - phase_start < phase_duration:
            futures = [
                executor.submit(workload.mixed_workload, worker_id, 10) for worker_id in range(num_workers)
            ]
            for future in futures:
                future.result()
            iteration_count += 1

        print(f"  ✓ Completed {iteration_count} iterations")

    elapsed = time.time() - start_time
    print(f"\n{'='*80}")
    print(f"Stress Test Completed")
    print(f"Total Duration: {elapsed:.2f} seconds")
    print(f"Final Counter Value: {workload.counter}")
    print(f"Cache Size: {len(workload.cache)}")
    print(f"Shared Resource Size: {len(workload.shared_resource)}")
    print(f"{'='*80}")


def main():
    """Main entry point"""
    # Parse command line arguments
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    num_workers = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    print("\n" + "="*80)
    print("RLock Profiling Stress Test")
    print("="*80)
    print("\nThis test will generate RLock contention patterns that should be")
    print("visible in the Datadog profiling UI with:")
    print("  - Lock acquire samples (showing where locks are acquired)")
    print("  - Lock release samples (showing where locks are released)")
    print("  - Lock wait time samples (showing contention)")
    print("  - Reentrant lock patterns (multiple acquisitions by same thread)")
    print("\nMonitor the profiling data at:")
    print("  https://app.datadoghq.com/profiling")
    print(f"\nService: {os.environ['DD_SERVICE']}")
    print(f"Environment: {os.environ['DD_ENV']}")

    # Run the stress test
    run_stress_test(duration_seconds=duration, num_workers=num_workers)

    # Keep the program alive for a bit to ensure profiles are uploaded
    print("\nWaiting 10 seconds for final profile upload...")
    time.sleep(10)

    print("\n✅ Stress test completed successfully!")
    print("Check the Datadog profiling UI for lock samples.")


if __name__ == "__main__":
    main()

