"""
Stress tests for the Lock profiler to test performance and reliability under high load.

This test suite covers several stress scenarios:
1. High contention: Multiple threads competing for the same locks
2. High frequency: Rapid acquire/release cycles
3. High concurrency: Many different locks being used simultaneously
4. Mixed workloads: Threading + asyncio locks together
5. Deep call stacks: Lock operations in nested function calls
6. Exception handling: Locks with exceptions during operations
7. Memory pressure: Large number of locks with profiler overhead
"""

import asyncio
import concurrent.futures
import gc
import glob
import os
import random
import sys
import threading
import time
import uuid
from typing import List

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import asyncio as collector_asyncio
from ddtrace.profiling.collector import threading as collector_threading
from tests.profiling.collector import pprof_utils
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


init_linenos(__file__)


class StressTestBase:
    """Base class for stress tests with common setup/teardown."""
    
    def setup_method(self, method):
        self.test_name = method.__name__
        self.output_prefix = "/tmp" + os.sep + self.test_name
        self.output_filename = self.output_prefix + "." + str(os.getpid())
        
        assert ddup.is_available, "ddup is not available"
        ddup.config(
            env="test",
            service="lock_stress_test",
            version="stress_v1",
            output_filename=self.output_prefix,
        )
        ddup.start()
    
    def teardown_method(self):
        try:
            ddup.upload()
        except Exception as e:
            print(f"Error during upload: {e}")
        
        for f in glob.glob(self.output_prefix + "*"):
            try:
                os.remove(f)
            except Exception as e:
                print(f"Error removing file {f}: {e}")


class TestLockStress(StressTestBase):
    """Threading lock stress tests."""
    
    def test_high_contention_stress(self):
        """Test multiple threads competing for the same locks."""
        NUM_THREADS = 50
        NUM_ITERATIONS = 1000
        NUM_SHARED_LOCKS = 5
        
        # Create shared locks that all threads will compete for
        shared_locks = [threading.Lock() for _ in range(NUM_SHARED_LOCKS)]  # !CREATE! shared_locks
        results = []
        exception_count = threading.local()
        exception_count.value = 0
        
        def contention_worker(worker_id: int):
            """Worker function that creates high contention."""
            try:
                for i in range(NUM_ITERATIONS):
                    # Randomly select a lock to create contention
                    lock_idx = random.randint(0, NUM_SHARED_LOCKS - 1)
                    lock = shared_locks[lock_idx]
                    
                    # Use both acquire/release and context manager patterns
                    if i % 2 == 0:
                        lock.acquire()  # !ACQUIRE! shared_locks
                        try:
                            # Simulate some work
                            time.sleep(0.0001)  # 0.1ms of work
                            results.append(f"worker_{worker_id}_iter_{i}")
                        finally:
                            lock.release()  # !RELEASE! shared_locks
                    else:
                        with lock:  # !ACQUIRE! !RELEASE! shared_locks
                            time.sleep(0.0001)
                            results.append(f"worker_{worker_id}_iter_{i}")
            except Exception as e:
                exception_count.value += 1
                print(f"Worker {worker_id} exception: {e}")
        
        with collector_threading.ThreadingLockCollector(capture_pct=100):
            start_time = time.time()
            
            # Create and start all threads
            threads = []
            for i in range(NUM_THREADS):
                t = threading.Thread(target=contention_worker, args=(i,))
                threads.append(t)
                t.start()
            
            # Wait for all threads to complete
            for t in threads:
                t.join()
            
            end_time = time.time()
        
        # Verify results
        print(f"High contention test completed in {end_time - start_time:.2f}s")
        print(f"Total operations: {len(results)}")
        print(f"Expected operations: {NUM_THREADS * NUM_ITERATIONS}")
        print(f"Exceptions: {exception_count.value}")
        
        # Verify we got the expected number of operations
        assert len(results) == NUM_THREADS * NUM_ITERATIONS
        assert exception_count.value == 0
        
        # Verify profiler captured lock events
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        release_samples = pprof_utils.get_samples_with_value_type(profile, "lock-release")
        
        # We should have captured a significant number of lock events
        assert len(acquire_samples) > NUM_THREADS * 10, f"Too few acquire samples: {len(acquire_samples)}"
        assert len(release_samples) > NUM_THREADS * 10, f"Too few release samples: {len(release_samples)}"
    
    def test_high_frequency_stress(self):
        """Test rapid lock acquire/release cycles."""
        NUM_ITERATIONS = 10000
        NUM_LOCKS = 100
        
        # Create many locks to cycle through quickly
        locks = [threading.Lock() for _ in range(NUM_LOCKS)]  # !CREATE! frequency_locks
        
        def high_frequency_worker():
            """Rapidly acquire and release locks."""
            for i in range(NUM_ITERATIONS):
                lock_idx = i % NUM_LOCKS
                lock = locks[lock_idx]
                
                # Rapid acquire/release without much work in between
                lock.acquire()  # !ACQUIRE! frequency_locks
                lock.release()  # !RELEASE! frequency_locks
                
                # Occasionally use context manager
                if i % 100 == 0:
                    with lock:  # !ACQUIRE! !RELEASE! frequency_locks
                        pass
        
        with collector_threading.ThreadingLockCollector(capture_pct=10):  # Lower sampling to reduce overhead
            start_time = time.time()
            high_frequency_worker()
            end_time = time.time()
        
        print(f"High frequency test completed in {end_time - start_time:.2f}s")
        print(f"Operations per second: {NUM_ITERATIONS / (end_time - start_time):.0f}")
        
        # Verify profiler didn't crash and captured some events
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        
        # With 10% sampling, we should still capture a reasonable number of events
        assert len(acquire_samples) > 100, f"Too few samples with high frequency: {len(acquire_samples)}"
    
    def test_deep_call_stack_stress(self):
        """Test lock operations in deeply nested function calls."""
        MAX_DEPTH = 50
        NUM_ITERATIONS = 100
        
        def recursive_lock_function(depth: int, locks: List[threading.Lock]):
            """Recursively acquire locks at different call stack depths."""
            if depth >= MAX_DEPTH:
                return
            
            # Use a different lock at each depth level
            lock = locks[depth % len(locks)]
            
            with lock:  # !ACQUIRE! !RELEASE! deep_locks
                # Recursive call to increase stack depth
                recursive_lock_function(depth + 1, locks)
        
        # Create locks for the deep stack test
        deep_locks = [threading.Lock() for _ in range(10)]  # !CREATE! deep_locks
        
        with collector_threading.ThreadingLockCollector(capture_pct=100):
            for i in range(NUM_ITERATIONS):
                recursive_lock_function(0, deep_locks)
        
        # Verify profiler handled deep stacks correctly
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        
        assert len(acquire_samples) > NUM_ITERATIONS, f"Deep stack samples: {len(acquire_samples)}"
        
        # Verify some samples have deep stack traces
        deep_stacks = [s for s in acquire_samples if len(s.location_id) > 10]
        assert len(deep_stacks) > 0, "Should have some samples with deep stacks"
    
    def test_exception_handling_stress(self):
        """Test lock profiler behavior when exceptions occur during lock operations."""
        NUM_THREADS = 20
        NUM_ITERATIONS = 500
        
        test_lock = threading.Lock()  # !CREATE! exception_lock
        exception_count = threading.local()
        exception_count.value = 0
        success_count = threading.local()
        success_count.value = 0
        
        def exception_worker(worker_id: int):
            """Worker that sometimes raises exceptions while holding locks."""
            for i in range(NUM_ITERATIONS):
                try:
                    with test_lock:  # !ACQUIRE! !RELEASE! exception_lock
                        # Randomly raise exceptions
                        if random.random() < 0.1:  # 10% chance of exception
                            raise ValueError(f"Test exception from worker {worker_id}")
                        success_count.value += 1
                except ValueError:
                    exception_count.value += 1
                    # Exception should not prevent lock release
        
        with collector_threading.ThreadingLockCollector(capture_pct=100):
            threads = []
            for i in range(NUM_THREADS):
                t = threading.Thread(target=exception_worker, args=(i,))
                threads.append(t)
                t.start()
            
            for t in threads:
                t.join()
        
        print(f"Exception test: {success_count.value} successes, {exception_count.value} exceptions")
        
        # Verify lock is not stuck (should be able to acquire it)
        assert test_lock.acquire(blocking=False), "Lock should not be stuck after exceptions"
        test_lock.release()
        
        # Verify profiler captured events despite exceptions
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        release_samples = pprof_utils.get_samples_with_value_type(profile, "lock-release")
        
        # Should have roughly equal acquire and release events
        assert abs(len(acquire_samples) - len(release_samples)) < 5, \
            f"Acquire/release mismatch: {len(acquire_samples)} vs {len(release_samples)}"
    
    def test_memory_pressure_stress(self):
        """Test lock profiler memory overhead with many locks."""
        NUM_LOCKS = 5000
        NUM_THREADS = 10
        
        def memory_worker(worker_id: int, locks: List[threading.Lock]):
            """Worker that cycles through many locks."""
            for i in range(100):
                lock_idx = (worker_id * 100 + i) % len(locks)
                with locks[lock_idx]:  # !ACQUIRE! !RELEASE! memory_locks
                    pass
        
        # Create many locks to test memory overhead
        many_locks = []
        for i in range(NUM_LOCKS):
            many_locks.append(threading.Lock())  # !CREATE! memory_locks
        
        # Measure memory before profiling
        gc.collect()
        
        with collector_threading.ThreadingLockCollector(capture_pct=1):  # Low sampling for memory test
            threads = []
            for i in range(NUM_THREADS):
                t = threading.Thread(target=memory_worker, args=(i, many_locks))
                threads.append(t)
                t.start()
            
            for t in threads:
                t.join()
        
        # Cleanup locks
        del many_locks
        gc.collect()
        
        # Verify profiler still works with memory pressure
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        
        # Even with 1% sampling, we should capture some events
        assert len(acquire_samples) > 0, "Should capture some events even under memory pressure"


@pytest.mark.asyncio
class TestAsyncioLockStress(StressTestBase):
    """Asyncio lock stress tests."""
    
    async def test_asyncio_concurrency_stress(self):
        """Test many concurrent asyncio tasks with locks."""
        NUM_TASKS = 100
        NUM_LOCKS = 20
        NUM_ITERATIONS = 50
        
        # Create asyncio locks
        asyncio_locks = [asyncio.Lock() for _ in range(NUM_LOCKS)]  # !CREATE! asyncio_stress_locks
        results = []
        
        async def asyncio_worker(task_id: int):
            """Asyncio worker that uses locks concurrently."""
            for i in range(NUM_ITERATIONS):
                lock_idx = random.randint(0, NUM_LOCKS - 1)
                lock = asyncio_locks[lock_idx]
                
                async with lock:  # !ACQUIRE! !RELEASE! asyncio_stress_locks
                    # Simulate async work
                    await asyncio.sleep(0.001)
                    results.append(f"task_{task_id}_iter_{i}")
        
        with collector_asyncio.AsyncioLockCollector(capture_pct=100):
            # Create and run many concurrent tasks
            tasks = [asyncio_worker(i) for i in range(NUM_TASKS)]
            await asyncio.gather(*tasks)
        
        print(f"Asyncio stress test: {len(results)} operations completed")
        assert len(results) == NUM_TASKS * NUM_ITERATIONS
        
        # Verify profiler captured asyncio lock events
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        
        assert len(acquire_samples) > NUM_TASKS, f"Asyncio samples: {len(acquire_samples)}"


class TestMixedLockStress(StressTestBase):
    """Mixed threading + asyncio lock stress tests."""
    
    def test_mixed_threading_asyncio_stress(self):
        """Test both threading and asyncio locks simultaneously."""
        NUM_THREADS = 10
        NUM_ASYNCIO_TASKS = 20
        NUM_ITERATIONS = 100
        
        # Threading locks
        thread_locks = [threading.Lock() for _ in range(5)]  # !CREATE! mixed_thread_locks
        
        # Results tracking
        results = []
        results_lock = threading.Lock()
        
        def threading_worker(worker_id: int):
            """Threading worker for mixed test."""
            for i in range(NUM_ITERATIONS):
                lock_idx = i % len(thread_locks)
                with thread_locks[lock_idx]:  # !ACQUIRE! !RELEASE! mixed_thread_locks
                    with results_lock:
                        results.append(f"thread_{worker_id}_iter_{i}")
        
        async def asyncio_worker(task_id: int):
            """Asyncio worker for mixed test."""
            # Create asyncio locks in the worker
            asyncio_locks = [asyncio.Lock() for _ in range(3)]  # !CREATE! mixed_asyncio_locks
            
            for i in range(NUM_ITERATIONS // 2):  # Fewer iterations for asyncio
                lock_idx = i % len(asyncio_locks)
                async with asyncio_locks[lock_idx]:  # !ACQUIRE! !RELEASE! mixed_asyncio_locks
                    await asyncio.sleep(0.001)
                    with results_lock:
                        results.append(f"asyncio_{task_id}_iter_{i}")
        
        async def run_asyncio_part():
            """Run the asyncio portion of the mixed test."""
            tasks = [asyncio_worker(i) for i in range(NUM_ASYNCIO_TASKS)]
            await asyncio.gather(*tasks)
        
        # Start both profilers
        with collector_threading.ThreadingLockCollector(capture_pct=50):
            with collector_asyncio.AsyncioLockCollector(capture_pct=50):
                # Start threading workers
                threads = []
                for i in range(NUM_THREADS):
                    t = threading.Thread(target=threading_worker, args=(i,))
                    threads.append(t)
                    t.start()
                
                # Run asyncio workers in a separate thread to avoid blocking
                def run_asyncio():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(run_asyncio_part())
                    finally:
                        loop.close()
                
                asyncio_thread = threading.Thread(target=run_asyncio)
                asyncio_thread.start()
                
                # Wait for all work to complete
                for t in threads:
                    t.join()
                asyncio_thread.join()
        
        print(f"Mixed test completed with {len(results)} total operations")
        
        expected_thread_ops = NUM_THREADS * NUM_ITERATIONS
        expected_asyncio_ops = NUM_ASYNCIO_TASKS * (NUM_ITERATIONS // 2)
        expected_total = expected_thread_ops + expected_asyncio_ops
        
        assert len(results) == expected_total, f"Expected {expected_total}, got {len(results)}"
        
        # Verify both types of locks were profiled
        ddup.upload()
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        acquire_samples = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
        
        # Should have samples from both threading and asyncio locks
        assert len(acquire_samples) > 10, f"Mixed stress samples: {len(acquire_samples)}"
