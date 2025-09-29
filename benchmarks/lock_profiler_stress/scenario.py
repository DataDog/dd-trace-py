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
- Memory pressure scenarios
- Deadlock-prone scenarios
- Async/await patterns
"""

import asyncio
import concurrent.futures
import queue
import random
import threading
import time
from typing import Callable, Generator, List, Optional
import weakref
import gc

import bm


class LockProfilerStress(bm.Scenario):
    """Base class for lock profiler stress test scenarios."""
    
    scenario_type: str
    num_threads: int = 10
    operations_per_thread: int = 1000
    
    def __init__(self):
        super().__init__()
        self.stats = {
            'total_operations': 0,
            'total_acquire_time': 0,
            'max_acquire_time': 0,
            'contentions': 0,
            'errors': 0,
        }
        self.start_time = None
        self.locks = []
        
    def _record_acquire_time(self, acquire_time: float):
        """Record lock acquisition timing for statistics."""
        self.stats['total_acquire_time'] += acquire_time
        self.stats['max_acquire_time'] = max(self.stats['max_acquire_time'], acquire_time)
        if acquire_time > 0.001:  # More than 1ms indicates contention
            self.stats['contentions'] += 1
    
    def _create_locks(self, num_locks: int) -> List[threading.Lock]:
        """Create a list of locks for testing."""
        return [threading.Lock() for _ in range(num_locks)]
    
    def run(self) -> Generator[Callable[[int], None], None, None]:
        """Run the stress test scenario."""
        scenario_method = getattr(self, f'_run_{self.scenario_type}', None)
        if not scenario_method:
            raise ValueError(f"Unknown scenario type: {self.scenario_type}")
        
        def _(loops: int) -> None:
            self.start_time = time.time()
            for _ in range(loops):
                scenario_method()
            
            # Print statistics
            duration = time.time() - self.start_time
            print(f"\nScenario: {self.scenario_type}")
            print(f"Duration: {duration:.2f}s")
            print(f"Total operations: {self.stats['total_operations']}")
            print(f"Operations/sec: {self.stats['total_operations'] / duration:.2f}")
            print(f"Avg acquire time: {self.stats['total_acquire_time'] / max(1, self.stats['total_operations']) * 1000:.3f}ms")
            print(f"Max acquire time: {self.stats['max_acquire_time'] * 1000:.3f}ms")
            print(f"Contentions: {self.stats['contentions']}")
            print(f"Errors: {self.stats['errors']}")
        
        yield _

    def _run_high_contention(self):
        """High contention scenario - many threads competing for few locks."""
        num_locks = getattr(self, 'num_locks', 2)
        hold_time_ms = getattr(self, 'hold_time_ms', 5)
        think_time_ms = getattr(self, 'think_time_ms', 1)
        
        locks = self._create_locks(num_locks)
        
        def worker():
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                start = time.time()
                try:
                    if lock.acquire(timeout=1.0):
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        # Simulate work while holding lock
                        time.sleep(hold_time_ms / 1000.0)
                        lock.release()
                        
                        # Think time between operations
                        if think_time_ms > 0:
                            time.sleep(think_time_ms / 1000.0)
                    else:
                        self.stats['errors'] += 1
                except Exception:
                    self.stats['errors'] += 1
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_lock_hierarchy(self):
        """Lock hierarchy scenario - nested locks with potential for deadlocks."""
        hierarchy_depth = getattr(self, 'hierarchy_depth', 3)
        hold_time_ms = getattr(self, 'hold_time_ms', 2)
        
        # Create a hierarchy of locks
        locks = self._create_locks(hierarchy_depth)
        
        def worker():
            for _ in range(self.operations_per_thread):
                # Always acquire locks in the same order to avoid deadlocks
                acquired_locks = []
                start = time.time()
                
                try:
                    # Acquire locks in order
                    for i in range(hierarchy_depth):
                        if locks[i].acquire(timeout=0.5):
                            acquired_locks.append(locks[i])
                        else:
                            self.stats['errors'] += 1
                            break
                    
                    if len(acquired_locks) == hierarchy_depth:
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        # Simulate work
                        time.sleep(hold_time_ms / 1000.0)
                
                finally:
                    # Release locks in reverse order
                    for lock in reversed(acquired_locks):
                        try:
                            lock.release()
                        except Exception:
                            self.stats['errors'] += 1
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_high_frequency(self):
        """High frequency scenario - rapid acquire/release cycles."""
        num_locks = getattr(self, 'num_locks', 10)
        hold_time_ms = getattr(self, 'hold_time_ms', 0)
        think_time_ms = getattr(self, 'think_time_ms', 0)
        
        locks = self._create_locks(num_locks)
        
        def worker():
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                start = time.time()
                try:
                    if lock.acquire(timeout=0.1):
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        if hold_time_ms > 0:
                            time.sleep(hold_time_ms / 1000.0)
                        
                        lock.release()
                        
                        if think_time_ms > 0:
                            time.sleep(think_time_ms / 1000.0)
                    else:
                        self.stats['errors'] += 1
                except Exception:
                    self.stats['errors'] += 1
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_mixed_primitives(self):
        """Mixed primitives scenario - various synchronization primitives."""
        use_rlock = getattr(self, 'use_rlock', True)
        use_condition = getattr(self, 'use_condition', True)
        use_semaphore = getattr(self, 'use_semaphore', True)
        use_event = getattr(self, 'use_event', True)
        
        # Create different types of synchronization primitives
        primitives = []
        primitives.extend(self._create_locks(5))  # Regular locks
        
        if use_rlock:
            primitives.extend([threading.RLock() for _ in range(3)])
        
        if use_semaphore:
            primitives.extend([threading.Semaphore(2) for _ in range(3)])
        
        condition_lock = threading.Lock()
        if use_condition:
            primitives.append(threading.Condition(condition_lock))
        
        events = []
        if use_event:
            events = [threading.Event() for _ in range(3)]
            for event in events:
                event.set()  # Start in set state
        
        def worker():
            for _ in range(self.operations_per_thread):
                primitive = random.choice(primitives)
                start = time.time()
                
                try:
                    if isinstance(primitive, (threading.Lock, threading.RLock)):
                        if primitive.acquire(timeout=0.5):
                            acquire_time = time.time() - start
                            self._record_acquire_time(acquire_time)
                            self.stats['total_operations'] += 1
                            time.sleep(0.001)  # Brief work
                            primitive.release()
                    
                    elif isinstance(primitive, threading.Semaphore):
                        if primitive.acquire(timeout=0.5):
                            acquire_time = time.time() - start
                            self._record_acquire_time(acquire_time)
                            self.stats['total_operations'] += 1
                            time.sleep(0.001)
                            primitive.release()
                    
                    elif isinstance(primitive, threading.Condition):
                        if primitive.acquire(timeout=0.5):
                            acquire_time = time.time() - start
                            self._record_acquire_time(acquire_time)
                            self.stats['total_operations'] += 1
                            # Randomly notify or wait briefly
                            if random.random() < 0.1:
                                primitive.notify_all()
                            primitive.release()
                
                except Exception:
                    self.stats['errors'] += 1
                
                # Occasionally work with events
                if events and random.random() < 0.1:
                    event = random.choice(events)
                    if event.wait(timeout=0.01):
                        event.clear()
                        time.sleep(0.001)
                        event.set()
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_long_held_locks(self):
        """Long-held locks scenario - simulating slow operations."""
        num_locks = getattr(self, 'num_locks', 5)
        hold_time_ms = getattr(self, 'hold_time_ms', 100)
        variance_ms = getattr(self, 'variance_ms', 50)
        
        locks = self._create_locks(num_locks)
        
        def worker():
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                start = time.time()
                try:
                    if lock.acquire(timeout=2.0):
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        # Variable hold time to simulate real-world scenarios
                        hold_time = hold_time_ms + random.randint(-variance_ms, variance_ms)
                        hold_time = max(1, hold_time)  # At least 1ms
                        time.sleep(hold_time / 1000.0)
                        
                        lock.release()
                    else:
                        self.stats['errors'] += 1
                except Exception:
                    self.stats['errors'] += 1
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_producer_consumer(self):
        """Producer-consumer scenario."""
        num_producers = getattr(self, 'num_producers', 10)
        num_consumers = getattr(self, 'num_consumers', 10)
        queue_size = getattr(self, 'queue_size', 100)
        items_per_producer = getattr(self, 'items_per_producer', 1000)
        processing_time_ms = getattr(self, 'processing_time_ms', 1)
        
        work_queue = queue.Queue(maxsize=queue_size)
        
        def producer(producer_id):
            for i in range(items_per_producer):
                try:
                    item = f"producer-{producer_id}-item-{i}"
                    work_queue.put(item, timeout=1.0)
                    self.stats['total_operations'] += 1
                except queue.Full:
                    self.stats['errors'] += 1
        
        def consumer():
            while True:
                try:
                    item = work_queue.get(timeout=0.1)
                    if item is None:  # Sentinel to stop
                        break
                    
                    # Simulate processing
                    time.sleep(processing_time_ms / 1000.0)
                    work_queue.task_done()
                    self.stats['total_operations'] += 1
                    
                except queue.Empty:
                    continue
                except Exception:
                    self.stats['errors'] += 1
        
        threads = []
        
        # Start producers
        for i in range(num_producers):
            t = threading.Thread(target=producer, args=(i,))
            threads.append(t)
            t.start()
        
        # Start consumers
        for _ in range(num_consumers):
            t = threading.Thread(target=consumer)
            threads.append(t)
            t.start()
        
        # Wait for producers to finish
        for t in threads[:num_producers]:
            t.join()
        
        # Signal consumers to stop
        for _ in range(num_consumers):
            work_queue.put(None)
        
        # Wait for consumers to finish
        for t in threads[num_producers:]:
            t.join()

    def _run_reader_writer(self):
        """Reader-writer scenario using locks to simulate read-write patterns."""
        num_readers = getattr(self, 'num_readers', 30)
        num_writers = getattr(self, 'num_writers', 5)
        read_time_ms = getattr(self, 'read_time_ms', 10)
        write_time_ms = getattr(self, 'write_time_ms', 50)
        
        # Simulate reader-writer lock with regular locks
        read_count_lock = threading.Lock()
        write_lock = threading.Lock()
        read_count = [0]  # Use list for mutable reference
        
        def reader():
            for _ in range(self.operations_per_thread):
                start = time.time()
                try:
                    # Acquire read access
                    read_count_lock.acquire()
                    read_count[0] += 1
                    if read_count[0] == 1:
                        # First reader locks out writers
                        write_lock.acquire(timeout=1.0)
                    read_count_lock.release()
                    
                    acquire_time = time.time() - start
                    self._record_acquire_time(acquire_time)
                    self.stats['total_operations'] += 1
                    
                    # Simulate reading
                    time.sleep(read_time_ms / 1000.0)
                    
                    # Release read access
                    read_count_lock.acquire()
                    read_count[0] -= 1
                    if read_count[0] == 0:
                        # Last reader releases writer lock
                        try:
                            write_lock.release()
                        except RuntimeError:
                            pass  # Already released
                    read_count_lock.release()
                
                except Exception:
                    self.stats['errors'] += 1
        
        def writer():
            for _ in range(self.operations_per_thread):
                start = time.time()
                try:
                    if write_lock.acquire(timeout=2.0):
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        # Simulate writing
                        time.sleep(write_time_ms / 1000.0)
                        write_lock.release()
                    else:
                        self.stats['errors'] += 1
                except Exception:
                    self.stats['errors'] += 1
        
        threads = []
        
        # Start readers
        for _ in range(num_readers):
            t = threading.Thread(target=reader)
            threads.append(t)
            t.start()
        
        # Start writers
        for _ in range(num_writers):
            t = threading.Thread(target=writer)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_memory_pressure(self):
        """Memory pressure scenario - allocations while holding locks."""
        num_locks = getattr(self, 'num_locks', 10)
        allocate_mb_per_op = getattr(self, 'allocate_mb_per_op', 1)
        hold_time_ms = getattr(self, 'hold_time_ms', 10)
        
        locks = self._create_locks(num_locks)
        
        def worker():
            allocated_objects = []
            for _ in range(self.operations_per_thread):
                lock = random.choice(locks)
                start = time.time()
                try:
                    if lock.acquire(timeout=1.0):
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        # Allocate memory while holding lock
                        size = allocate_mb_per_op * 1024 * 1024 // 8  # 8 bytes per float
                        data = [random.random() for _ in range(size)]
                        allocated_objects.append(data)
                        
                        # Periodically clean up to avoid OOM
                        if len(allocated_objects) > 10:
                            allocated_objects.pop(0)
                        
                        time.sleep(hold_time_ms / 1000.0)
                        lock.release()
                    else:
                        self.stats['errors'] += 1
                except Exception:
                    self.stats['errors'] += 1
            
            # Clean up
            del allocated_objects
            gc.collect()
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_deadlock_prone(self):
        """Deadlock-prone scenario - acquire locks in random order with timeouts."""
        num_locks = getattr(self, 'num_locks', 5)
        randomize_order = getattr(self, 'randomize_order', True)
        timeout_ms = getattr(self, 'timeout_ms', 100)
        
        locks = self._create_locks(num_locks)
        
        def worker():
            for _ in range(self.operations_per_thread):
                # Select a subset of locks to acquire
                num_to_acquire = random.randint(2, min(4, num_locks))
                selected_locks = random.sample(locks, num_to_acquire)
                
                if not randomize_order:
                    # Sort by id to avoid deadlocks
                    selected_locks.sort(key=lambda x: id(x))
                else:
                    # Random order - might cause contention/timeouts
                    random.shuffle(selected_locks)
                
                acquired_locks = []
                start = time.time()
                
                try:
                    # Try to acquire all selected locks
                    for lock in selected_locks:
                        if lock.acquire(timeout=timeout_ms / 1000.0):
                            acquired_locks.append(lock)
                        else:
                            self.stats['errors'] += 1
                            break
                    
                    if len(acquired_locks) == len(selected_locks):
                        acquire_time = time.time() - start
                        self._record_acquire_time(acquire_time)
                        self.stats['total_operations'] += 1
                        
                        # Brief work
                        time.sleep(0.001)
                
                finally:
                    # Release all acquired locks
                    for lock in acquired_locks:
                        try:
                            lock.release()
                        except Exception:
                            self.stats['errors'] += 1
        
        threads = []
        for _ in range(self.num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()

    def _run_async_stress(self):
        """Async stress scenario using asyncio locks."""
        num_tasks = getattr(self, 'num_tasks', 100)
        num_locks = getattr(self, 'num_locks', 10)
        operations_per_task = getattr(self, 'operations_per_task', 500)
        async_sleep_ms = getattr(self, 'async_sleep_ms', 1)
        
        async def run_async_scenario():
            # Create async locks
            locks = [asyncio.Lock() for _ in range(num_locks)]
            
            async def async_worker():
                for _ in range(operations_per_task):
                    lock = random.choice(locks)
                    start = time.time()
                    try:
                        async with lock:
                            acquire_time = time.time() - start
                            self._record_acquire_time(acquire_time)
                            self.stats['total_operations'] += 1
                            
                            # Async work
                            await asyncio.sleep(async_sleep_ms / 1000.0)
                    except Exception:
                        self.stats['errors'] += 1
            
            # Create and run tasks
            tasks = [async_worker() for _ in range(num_tasks)]
            await asyncio.gather(*tasks)
        
        # Run the async scenario
        asyncio.run(run_async_scenario())
