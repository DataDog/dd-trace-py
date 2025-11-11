"""
Lock Profiler Stress Test Scenarios

This module implements various stress test scenarios designed to thoroughly
test the lock profiling capabilities under different conditions including:
- High contention scenarios
- Lock hierarchies and nested locks
- High frequency lock operations
- Mixed synchronization primitives
- Long-held locks
- Producer-consumer patterns
- Reader-writer patterns
- Deadlock-prone scenarios
"""

import queue
import random
import threading
import time
from typing import Callable, Generator

import bm


class LockProfilerStress(bm.Scenario):
    """Base class for lock profiler stress test scenarios."""

    scenario_type: str
    num_threads: int = 10
    operations_per_thread: int = 1000
    
    # Optional parameters used by different scenarios
    num_locks: int = 2
    hold_time_ms: int = 5
    think_time_ms: int = 1
    hierarchy_depth: int = 3
    use_rlock: bool = True
    use_semaphore: bool = True
    variance_ms: int = 50
    num_producers: int = 10
    num_consumers: int = 10
    queue_size: int = 100
    items_per_producer: int = 1000
    processing_time_ms: int = 1
    num_readers: int = 30
    num_writers: int = 5
    read_time_ms: int = 10
    write_time_ms: int = 50
    randomize_order: bool = True
    timeout_ms: int = 100

    def _create_locks(self, num_locks: int):
        """Create a list of locks for testing."""
        return [threading.Lock() for _ in range(num_locks)]

    def run(self) -> Generator[Callable[[int], None], None, None]:
        """Run the stress test scenario."""
        scenario_method = getattr(self, f"_run_{self.scenario_type}", None)
        if not scenario_method:
            raise ValueError(f"Unknown scenario type: {self.scenario_type}")

        def _(loops: int) -> None:
            for _ in range(loops):
                scenario_method()

        yield _

    def _run_high_contention(self):
        """High contention scenario - many threads competing for few locks."""
        locks = self._create_locks(self.num_locks)

        def worker():
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                try:
                    if lock.acquire(timeout=1.0):
                        time.sleep(self.hold_time_ms / 1000.0)
                        lock.release()
                        if self.think_time_ms > 0:
                            time.sleep(self.think_time_ms / 1000.0)
                except Exception:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_lock_hierarchy(self):
        """Lock hierarchy scenario - nested locks."""
        hierarchy_depth = self.hierarchy_depth
        hold_time_ms = self.hold_time_ms

        locks = self._create_locks(hierarchy_depth)

        def worker():
            for _ in range(self.operations_per_thread):
                acquired_locks = []
                try:
                    for lock in locks:
                        if lock.acquire(timeout=0.5):
                            acquired_locks.append(lock)
                        else:
                            break
                    if len(acquired_locks) == hierarchy_depth:
                        time.sleep(hold_time_ms / 1000.0)
                finally:
                    for lock in reversed(acquired_locks):
                        try:
                            lock.release()
                        except Exception:
                            pass

        threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_high_frequency(self):
        """High frequency scenario - rapid acquire/release cycles."""
        num_locks = self.num_locks
        hold_time_ms = self.hold_time_ms
        think_time_ms = self.think_time_ms

        locks = self._create_locks(num_locks)

        def worker():
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                try:
                    if lock.acquire(timeout=0.1):
                        if hold_time_ms > 0:
                            time.sleep(hold_time_ms / 1000.0)
                        lock.release()
                        if think_time_ms > 0:
                            time.sleep(think_time_ms / 1000.0)
                except Exception:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_mixed_primitives(self):
        """Mixed primitives scenario - various synchronization primitives."""
        use_rlock = self.use_rlock
        use_semaphore = self.use_semaphore

        primitives = []
        primitives.extend(self._create_locks(5))

        if use_rlock:
            primitives.extend([threading.RLock() for _ in range(3)])

        if use_semaphore:
            primitives.extend([threading.Semaphore(2) for _ in range(3)])

        def worker():
            for _ in range(self.operations_per_thread):
                primitive = random.choice(primitives)
                try:
                    if isinstance(primitive, (threading.Lock, threading.RLock, threading.Semaphore)):
                        if primitive.acquire(timeout=0.5):
                            time.sleep(0.001)
                            primitive.release()
                except Exception:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_long_held_locks(self):
        """Long-held locks scenario - simulating slow operations."""
        num_locks = self.num_locks
        hold_time_ms = self.hold_time_ms
        variance_ms = self.variance_ms

        locks = self._create_locks(num_locks)

        def worker():
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                try:
                    if lock.acquire(timeout=2.0):
                        hold_time = hold_time_ms + random.randint(-variance_ms, variance_ms)
                        hold_time = max(1, hold_time)
                        time.sleep(hold_time / 1000.0)
                        lock.release()
                except Exception:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_producer_consumer(self):
        """Producer-consumer scenario."""
        num_producers = self.num_producers
        num_consumers = self.num_consumers
        queue_size = self.queue_size
        items_per_producer = self.items_per_producer
        processing_time_ms = self.processing_time_ms

        work_queue = queue.Queue(maxsize=queue_size)

        def producer(producer_id):
            for i in range(items_per_producer):
                try:
                    item = f"producer-{producer_id}-item-{i}"
                    work_queue.put(item, timeout=1.0)
                except queue.Full:
                    pass

        def consumer():
            while True:
                try:
                    item = work_queue.get(timeout=0.1)
                    if item is None:
                        break
                    time.sleep(processing_time_ms / 1000.0)
                    work_queue.task_done()
                except queue.Empty:
                    continue
                except Exception:
                    pass

        threads = []
        for i in range(num_producers):
            threads.append(threading.Thread(target=producer, args=(i,)))
        for _ in range(num_consumers):
            threads.append(threading.Thread(target=consumer))

        for t in threads:
            t.start()

        for t in threads[:num_producers]:
            t.join()

        for _ in range(num_consumers):
            work_queue.put(None)

        for t in threads[num_producers:]:
            t.join()

    def _run_reader_writer(self):
        """Reader-writer scenario using locks to simulate read-write patterns."""
        num_readers = self.num_readers
        num_writers = self.num_writers
        read_time_ms = self.read_time_ms
        write_time_ms = self.write_time_ms

        read_count_lock = threading.Lock()
        write_lock = threading.Lock()
        read_count = [0]

        def reader():
            for _ in range(self.operations_per_thread):
                try:
                    read_count_lock.acquire()
                    read_count[0] += 1
                    if read_count[0] == 1:
                        write_lock.acquire(timeout=1.0)
                    read_count_lock.release()

                    time.sleep(read_time_ms / 1000.0)

                    read_count_lock.acquire()
                    read_count[0] -= 1
                    if read_count[0] == 0:
                        try:
                            write_lock.release()
                        except RuntimeError:
                            pass
                    read_count_lock.release()
                except Exception:
                    pass

        def writer():
            for _ in range(self.operations_per_thread):
                try:
                    if write_lock.acquire(timeout=2.0):
                        time.sleep(write_time_ms / 1000.0)
                        write_lock.release()
                except Exception:
                    pass

        threads = []
        for _ in range(num_readers):
            threads.append(threading.Thread(target=reader))
        for _ in range(num_writers):
            threads.append(threading.Thread(target=writer))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run_deadlock_prone(self):
        """Deadlock-prone scenario - acquire locks in random order with timeouts."""
        num_locks = self.num_locks
        randomize_order = self.randomize_order
        timeout_ms = self.timeout_ms

        locks = self._create_locks(num_locks)

        def worker():
            for _ in range(self.operations_per_thread):
                num_to_acquire = random.randint(2, min(4, num_locks))
                selected_locks = random.sample(locks, num_to_acquire)

                if not randomize_order:
                    selected_locks.sort(key=lambda x: id(x))
                else:
                    random.shuffle(selected_locks)

                acquired_locks = []
                try:
                    for lock in selected_locks:
                        if lock.acquire(timeout=timeout_ms / 1000.0):
                            acquired_locks.append(lock)
                        else:
                            break

                    if len(acquired_locks) == len(selected_locks):
                        time.sleep(0.001)
                finally:
                    for lock in acquired_locks:
                        try:
                            lock.release()
                        except Exception:
                            pass

        threads = [threading.Thread(target=worker) for _ in range(self.num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
