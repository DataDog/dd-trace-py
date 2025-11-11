"""
Lock Profiler Performance Benchmarks

This module implements performance benchmarks designed to measure the overhead
of the lock profiler implementation under various conditions and sampling rates.

The benchmarks measure:
- Lock creation overhead
- Acquire/release performance
- Memory usage per lock
- Throughput under concurrent load
"""

import threading
import time
from typing import Callable, Generator

import bm


class LockProfilerPerformance(bm.Scenario):
    """Performance benchmarks for lock profiler."""

    benchmark_type: str
    num_threads: int = 10
    num_locks: int = 100
    operations_per_thread: int = 10000
    capture_pct: float = 0.0

    def run(self) -> Generator[Callable[[int], None], None, None]:
        """Run the performance benchmark."""
        benchmark_method = getattr(self, f"_bench_{self.benchmark_type}", None)
        if not benchmark_method:
            raise ValueError(f"Unknown benchmark type: {self.benchmark_type}")

        def _(loops: int) -> None:
            for _ in range(loops):
                benchmark_method()

        yield _

    def _bench_creation(self):
        """Measure lock creation overhead."""
        # Create many locks to measure creation time
        locks = [threading.Lock() for _ in range(self.num_locks)]
        # Keep locks alive
        _ = locks

    def _bench_acquire_release(self):
        """Measure acquire/release performance."""
        lock = threading.Lock()

        # Rapid acquire/release cycles
        for _ in range(self.operations_per_thread):
            lock.acquire()
            lock.release()

    def _bench_throughput(self):
        """Measure overall throughput under concurrent load."""
        lock = threading.Lock()
        counter = [0]

        def worker():
            for _ in range(self.operations_per_thread):
                with lock:
                    counter[0] += 1

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_contention(self):
        """Measure performance under high contention."""
        # Few locks, many threads competing
        num_locks = min(5, self.num_locks)
        locks = [threading.Lock() for _ in range(num_locks)]

        def worker():
            for i in range(self.operations_per_thread):
                # Use modulo to cycle through locks
                lock = locks[i % num_locks]
                with lock:
                    # Minimal work to maximize lock operations
                    pass

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_nested(self):
        """Measure performance with nested lock acquisitions."""
        depth = 5
        locks = [threading.Lock() for _ in range(depth)]

        def worker():
            for _ in range(self.operations_per_thread // depth):
                # Acquire locks in order
                for lock in locks:
                    lock.acquire()

                # Release in reverse order
                for lock in reversed(locks):
                    lock.release()

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_mixed_primitives(self):
        """Measure performance with mixed synchronization primitives."""
        regular_locks = [threading.Lock() for _ in range(10)]
        rlocks = [threading.RLock() for _ in range(5)]
        semaphores = [threading.Semaphore(2) for _ in range(5)]

        def worker():
            ops_per_type = self.operations_per_thread // 3

            # Regular locks
            for i in range(ops_per_type):
                lock = regular_locks[i % len(regular_locks)]
                with lock:
                    pass

            # RLocks
            for i in range(ops_per_type):
                rlock = rlocks[i % len(rlocks)]
                with rlock:
                    pass

            # Semaphores
            for i in range(ops_per_type):
                semaphore = semaphores[i % len(semaphores)]
                with semaphore:
                    pass

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_short_critical_section(self):
        """Measure performance with very short critical sections."""
        lock = threading.Lock()

        def worker():
            for _ in range(self.operations_per_thread):
                with lock:
                    # Empty critical section
                    pass

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_long_critical_section(self):
        """Measure performance with longer critical sections."""
        lock = threading.Lock()

        def worker():
            for _ in range(self.operations_per_thread // 100):  # Fewer iterations
                with lock:
                    # Simulate some work
                    time.sleep(0.001)  # 1ms

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_context_manager(self):
        """Measure performance using context manager (with statement)."""
        locks = [threading.Lock() for _ in range(self.num_locks)]

        def worker():
            for i in range(self.operations_per_thread):
                lock = locks[i % self.num_locks]
                with lock:
                    pass

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    def _bench_explicit_acquire_release(self):
        """Measure performance using explicit acquire/release."""
        locks = [threading.Lock() for _ in range(self.num_locks)]

        def worker():
            for i in range(self.operations_per_thread):
                lock = locks[i % self.num_locks]
                try:
                    lock.acquire()
                finally:
                    lock.release()

        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

