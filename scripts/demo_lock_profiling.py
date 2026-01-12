#!/usr/bin/env python
"""
Demo script for lock profiling with various primitives.

Usage:
  ddtrace-run python demo_lock_profiling.py [PRIMITIVE]

Primitives:
  threading-lock            threading.Lock
  threading-rlock           threading.RLock
  threading-condition       threading.Condition
  threading-semaphore       threading.Semaphore
  threading-bounded         threading.BoundedSemaphore
  asyncio-semaphore         asyncio.Semaphore
  asyncio-bounded           asyncio.BoundedSemaphore
  asyncio-condition         asyncio.Condition
  all                       All primitives

Environment:
  DD_SERVICE=lock-demo DD_ENV=dev DD_PROFILING_ENABLED=true ddtrace-run python scripts/demo_lock_profiling.py

Examples:
  ddtrace-run python demo_lock_profiling.py threading-lock
  ddtrace-run python demo_lock_profiling.py threading-condition
  ddtrace-run python demo_lock_profiling.py asyncio-condition
  ddtrace-run python demo_lock_profiling.py all
"""

import argparse
import asyncio
import random
import threading
import time
from typing import Callable
from typing import List


# =============================================================================
# PRIMITIVES - Define lock objects at module level for clear naming in UI
# =============================================================================

# Threading primitives
shared_counter_lock = threading.Lock()
reentrant_resource_lock = threading.RLock()
data_ready_condition = threading.Condition()
connection_pool = threading.Semaphore(3)
rate_limiter = threading.BoundedSemaphore(5)

# Shared state for condition demos
_threading_data_ready = False
_asyncio_data_ready = False

# Asyncio primitives (created lazily in async context)
async_connection_pool: asyncio.Semaphore = None  # type: ignore
async_rate_limiter: asyncio.BoundedSemaphore = None  # type: ignore
async_data_ready_condition: asyncio.Condition = None  # type: ignore


def init_asyncio_primitives() -> None:
    """Initialize asyncio primitives (must be called within event loop)."""
    global async_connection_pool, async_rate_limiter, async_data_ready_condition
    async_connection_pool = asyncio.Semaphore(3)
    async_rate_limiter = asyncio.BoundedSemaphore(5)
    async_data_ready_condition = asyncio.Condition()


# =============================================================================
# THREADING DEMOS
# =============================================================================


def demo_threading_lock() -> None:
    """Demo threading.Lock with contention."""
    print("\n[threading.Lock] Starting demo...")
    counter = [0]  # Mutable to allow modification in nested function

    def worker(worker_id: int) -> None:
        for i in range(50):
            with shared_counter_lock:
                counter[0] += 1
                time.sleep(random.uniform(0.01, 0.03))
            if i % 20 == 0:
                print(f"  Lock worker-{worker_id}: iteration {i}, counter={counter[0]}")

    _run_threaded_workers(worker, num_workers=6, prefix="Lock")
    print(f"  Final counter: {counter[0]}")


def demo_threading_rlock() -> None:
    """Demo threading.RLock with contention and reentrant access."""
    print("\n[threading.RLock] Starting demo...")

    def outer_operation() -> None:
        with reentrant_resource_lock:
            time.sleep(random.uniform(0.01, 0.02))
            inner_operation()  # Reentrant call

    def inner_operation() -> None:
        with reentrant_resource_lock:  # Same thread can acquire again
            time.sleep(random.uniform(0.01, 0.02))

    def worker(worker_id: int) -> None:
        for i in range(30):
            outer_operation()
            if i % 10 == 0:
                print(f"  RLock worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=6, prefix="RLock")


def demo_threading_condition() -> None:
    """Demo threading.Condition with producer/consumer pattern."""
    print("\n[threading.Condition] Starting demo...")
    global _threading_data_ready
    _threading_data_ready = False
    items_produced = [0]

    def producer() -> None:
        global _threading_data_ready
        for i in range(10):
            time.sleep(random.uniform(0.05, 0.1))
            with data_ready_condition:
                items_produced[0] += 1
                _threading_data_ready = True
                data_ready_condition.notify_all()
                print(f"  Producer: item {items_produced[0]} ready")
            _threading_data_ready = False

    def consumer(consumer_id: int) -> None:
        global _threading_data_ready
        consumed = 0
        for _ in range(5):
            with data_ready_condition:
                while not _threading_data_ready:
                    data_ready_condition.wait(timeout=0.5)
                consumed += 1
            time.sleep(random.uniform(0.02, 0.05))
        print(f"  Consumer-{consumer_id}: consumed {consumed} items")

    threads: List[threading.Thread] = []
    # Start consumers first
    for i in range(4):
        t = threading.Thread(target=consumer, args=(i,), name=f"Consumer-{i}")
        threads.append(t)
        t.start()
    # Start producer
    producer_thread = threading.Thread(target=producer, name="Producer")
    threads.append(producer_thread)
    producer_thread.start()
    # Wait for all
    for t in threads:
        t.join()


def demo_threading_semaphore() -> None:
    """Demo threading.Semaphore with contention."""
    print("\n[threading.Semaphore] Starting demo...")

    def worker(worker_id: int) -> None:
        for i in range(30):
            with connection_pool:
                time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  Semaphore worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=8, prefix="Semaphore")


def demo_threading_bounded_semaphore() -> None:
    """Demo threading.BoundedSemaphore with contention."""
    print("\n[threading.BoundedSemaphore] Starting demo...")

    def worker(worker_id: int) -> None:
        for i in range(30):
            with rate_limiter:
                time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  BoundedSemaphore worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=10, prefix="BoundedSem")


def _run_threaded_workers(target: Callable[[int], None], num_workers: int, prefix: str) -> None:
    """Helper to run threaded workers."""
    threads: List[threading.Thread] = []
    for i in range(num_workers):
        t = threading.Thread(target=target, args=(i,), name=f"{prefix}-{i}")
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


# =============================================================================
# ASYNCIO DEMOS
# =============================================================================


def demo_asyncio_semaphore() -> None:
    """Demo asyncio.Semaphore with contention."""
    print("\n[asyncio.Semaphore] Starting demo...")
    asyncio.run(_asyncio_semaphore_workload())


async def _asyncio_semaphore_workload() -> None:
    """Async workload for Semaphore demo."""
    init_asyncio_primitives()

    async def worker(worker_id: int) -> None:
        for i in range(30):
            async with async_connection_pool:
                await asyncio.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  Async Semaphore worker-{worker_id}: iteration {i}")

    await asyncio.gather(*[worker(i) for i in range(8)])


def demo_asyncio_bounded_semaphore() -> None:
    """Demo asyncio.BoundedSemaphore with contention."""
    print("\n[asyncio.BoundedSemaphore] Starting demo...")
    asyncio.run(_asyncio_bounded_workload())


async def _asyncio_bounded_workload() -> None:
    """Async workload for BoundedSemaphore demo."""
    init_asyncio_primitives()

    async def worker(worker_id: int) -> None:
        for i in range(30):
            async with async_rate_limiter:
                await asyncio.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  Async BoundedSem worker-{worker_id}: iteration {i}")

    await asyncio.gather(*[worker(i) for i in range(10)])


def demo_asyncio_condition() -> None:
    """Demo asyncio.Condition with producer/consumer pattern."""
    print("\n[asyncio.Condition] Starting demo...")
    asyncio.run(_asyncio_condition_workload())


async def _asyncio_condition_workload() -> None:
    """Async workload for Condition demo."""
    global _asyncio_data_ready
    init_asyncio_primitives()
    _asyncio_data_ready = False
    items_produced = [0]

    async def producer() -> None:
        global _asyncio_data_ready
        for i in range(10):
            await asyncio.sleep(random.uniform(0.05, 0.1))
            async with async_data_ready_condition:
                items_produced[0] += 1
                _asyncio_data_ready = True
                async_data_ready_condition.notify_all()
                print(f"  Async Producer: item {items_produced[0]} ready")
            _asyncio_data_ready = False

    async def consumer(consumer_id: int) -> None:
        global _asyncio_data_ready
        consumed = 0
        for _ in range(5):
            async with async_data_ready_condition:
                # Wait with timeout using wait_for pattern
                try:
                    await asyncio.wait_for(
                        async_data_ready_condition.wait_for(lambda: _asyncio_data_ready),
                        timeout=0.5,
                    )
                    consumed += 1
                except asyncio.TimeoutError:
                    pass
            await asyncio.sleep(random.uniform(0.02, 0.05))
        print(f"  Async Consumer-{consumer_id}: consumed {consumed} items")

    # Start producer and consumers concurrently
    await asyncio.gather(producer(), *[consumer(i) for i in range(4)])


# =============================================================================
# MAIN
# =============================================================================

PRIMITIVES = {
    # Threading
    "threading-lock": ("threading.Lock", demo_threading_lock),
    "threading-rlock": ("threading.RLock", demo_threading_rlock),
    "threading-condition": ("threading.Condition", demo_threading_condition),
    "threading-semaphore": ("threading.Semaphore", demo_threading_semaphore),
    "threading-bounded": ("threading.BoundedSemaphore", demo_threading_bounded_semaphore),
    # Asyncio
    "asyncio-semaphore": ("asyncio.Semaphore", demo_asyncio_semaphore),
    "asyncio-bounded": ("asyncio.BoundedSemaphore", demo_asyncio_bounded_semaphore),
    "asyncio-condition": ("asyncio.Condition", demo_asyncio_condition),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demo lock profiling with various primitives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ddtrace-run python demo_lock_profiling.py threading-lock
  ddtrace-run python demo_lock_profiling.py threading-condition
  ddtrace-run python demo_lock_profiling.py asyncio-condition
  ddtrace-run python demo_lock_profiling.py all
        """,
    )
    parser.add_argument(
        "primitive",
        nargs="?",
        default="threading-lock",
        choices=list(PRIMITIVES.keys()) + ["all"],
        help="Lock primitive to demo (default: threading-lock)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("LOCK PROFILING DEMO")
    print("=" * 70)

    if args.primitive == "all":
        demos = list(PRIMITIVES.values())
    else:
        demos = [PRIMITIVES[args.primitive]]

    print(f"\nPrimitives: {', '.join(name for name, _ in demos)}")
    print("\nExpected in Datadog UI (APM -> Profiler -> Lock Wait/Hold Time):")
    print("  - Lock names matching variable names in this file")
    print("  - Stack traces pointing to worker functions")
    print()

    start = time.time()
    for name, demo_fn in demos:
        demo_fn()
        print(f"  ✓ {name} done")

    print(f"\nCompleted in {time.time() - start:.2f}s")
    print("Waiting 5s for profiler to flush...")
    time.sleep(5)
    print("Done!")


if __name__ == "__main__":
    main()
