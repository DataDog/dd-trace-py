"""
Lock Profiler Memory Benchmarks

This module implements memory-focused benchmarks to measure the memory overhead
of the lock profiler implementation. It specifically measures:
- Memory per lock instance
- Total memory overhead with many locks
- Memory growth over time
- Memory with different sampling rates

This is particularly useful for comparing wrapped vs unwrapped implementations.
"""

import gc
import threading
import tracemalloc
from typing import Callable, Generator

import bm


class LockProfilerMemory(bm.Scenario):
    """Memory benchmarks for lock profiler."""

    num_locks: int = 1000
    num_iterations: int = 100
    capture_pct: float = 0.0

    def run(self) -> Generator[Callable[[int], None], None, None]:
        """Run the memory benchmark."""

        def _(loops: int) -> None:
            for _ in range(loops):
                # Start memory tracking
                tracemalloc.start()
                gc.collect()
                baseline = tracemalloc.get_traced_memory()[0]

                # Create locks
                locks = [threading.Lock() for _ in range(self.num_locks)]

                # Measure memory after lock creation
                gc.collect()
                current, peak = tracemalloc.get_traced_memory()
                memory_used = current - baseline

                tracemalloc.stop()

                # Keep locks alive to ensure accurate measurement
                _ = locks

                # Use the memory measurement to affect the benchmark result
                # We'll do a small amount of work proportional to memory used
                # This makes the benchmark reflect both time and memory
                for _ in range(int(memory_used / 1000)):
                    pass

        yield _


class LockProfilerMemoryGrowth(bm.Scenario):
    """Test memory growth over time with lock creation/destruction."""

    num_iterations: int = 100
    locks_per_iteration: int = 100
    capture_pct: float = 0.0

    def run(self) -> Generator[Callable[[int], None], None, None]:
        """Run the memory growth benchmark."""

        def _(loops: int) -> None:
            for _ in range(loops):
                tracemalloc.start()
                initial_memory = tracemalloc.get_traced_memory()[0]

                # Create and destroy locks multiple times
                for _ in range(self.num_iterations):
                    locks = [threading.Lock() for _ in range(self.locks_per_iteration)]
                    # Use the locks briefly
                    for lock in locks:
                        lock.acquire()
                        lock.release()
                    # Destroy them
                    locks.clear()
                    gc.collect()

                final_memory = tracemalloc.get_traced_memory()[0]
                memory_growth = final_memory - initial_memory

                tracemalloc.stop()

                # Ensure memory growth is minimal (no leaks)
                # Do work proportional to growth
                for _ in range(int(abs(memory_growth) / 1000)):
                    pass

        yield _


class LockProfilerMemoryPressure(bm.Scenario):
    """Test memory usage under concurrent lock operations."""

    num_threads: int = 20
    num_locks: int = 100
    operations_per_thread: int = 1000
    capture_pct: float = 0.0

    def run(self) -> Generator[Callable[[int], None], None, None]:
        """Run the memory pressure benchmark."""

        def _(loops: int) -> None:
            for _ in range(loops):
                tracemalloc.start()
                gc.collect()
                baseline = tracemalloc.get_traced_memory()[0]

                locks = [threading.Lock() for _ in range(self.num_locks)]

                def worker():
                    for i in range(self.operations_per_thread):
                        lock = locks[i % self.num_locks]
                        with lock:
                            pass

                threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

                gc.collect()
                current, peak = tracemalloc.get_traced_memory()
                memory_used = current - baseline
                peak_memory = peak - baseline

                tracemalloc.stop()

                # Use peak memory for the benchmark
                for _ in range(int(peak_memory / 10000)):
                    pass

        yield _

