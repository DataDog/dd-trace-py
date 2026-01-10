#!/usr/bin/env python3
"""
Profiler Backend Simulator - Simulates Datadog's profile processing service

This simulates the lock usage patterns of a profile processing service:
- Profile data ingestion and buffering
- Symbol resolution with caching
- Concurrent profile aggregation
- Rate limiting with semaphores
- Periodic metrics computation

Realistic lock counts: 20-50 locks per process
- Based on analysis of dd-trace-py's profiler:
  - Collectors: 1-2 locks each
  - Recorder: 1 RLock
  - Exporters: 1-2 locks each
  - Symbol cache: 1 RLock

Contention level: Medium (batch processing)
"""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
import logging
import random
import threading
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# 1. Profile Data Structures
# =============================================================================


@dataclass
class StackFrame:
    """A single stack frame"""

    function: str
    filename: str
    lineno: int

    def __hash__(self):
        return hash((self.function, self.filename, self.lineno))


@dataclass
class Sample:
    """A profiling sample"""

    stack: Tuple[StackFrame, ...]
    value: int  # CPU time, allocations, etc.
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class Profile:
    """A complete profile"""

    profile_id: str
    service: str
    env: str
    version: str
    start_time: float
    end_time: float
    samples: List[Sample] = field(default_factory=list)

    @property
    def duration_ns(self) -> int:
        return int((self.end_time - self.start_time) * 1e9)


# =============================================================================
# 2. Symbol Cache
#
# Based on dd-trace-py's caching patterns.
# Uses 1 RLock for thread-safe cache access.
# =============================================================================


class SymbolCache:
    """
    Cache for resolved symbols.

    Lock pattern: 1 RLock for cache access (like dd-trace-py's MsgpackStringTable)
    Real-world: Symbol resolution is expensive, heavy caching is critical.

    NOTE: We do NOT use per-symbol locks. That would create thousands of locks
    which is unrealistic. Instead, we use a single RLock like most caches.
    """

    def __init__(self, max_size: int = 100000):
        self._max_size = max_size
        self._cache: Dict[str, Any] = {}

        # Single RLock for the entire cache (like dd-trace-py)
        self._lock = threading.RLock()

        # LRU tracking (simplified)
        self._access_order: List[str] = []

        # Stats
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get a cached symbol"""
        with self._lock:
            if key in self._cache:
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        """Cache a resolved symbol"""
        with self._lock:
            if key in self._cache:
                return

            # Evict if necessary
            if len(self._cache) >= self._max_size:
                self._evict_lru()

            self._cache[key] = value
            self._access_order.append(key)

    def _evict_lru(self) -> None:
        """Evict least recently used entries (must hold lock)"""
        if self._access_order:
            evict_count = max(1, len(self._access_order) // 10)
            keys_to_evict = self._access_order[:evict_count]
            self._access_order = self._access_order[evict_count:]

            for key in keys_to_evict:
                self._cache.pop(key, None)

    def resolve_or_fetch(self, key: str, resolver_fn) -> Any:
        """Get from cache or resolve and cache"""
        # Check cache first
        cached = self.get(key)
        if cached is not None:
            return cached

        # Resolve (outside lock to avoid blocking)
        value = resolver_fn(key)
        self.put(key, value)
        return value

    def stats(self) -> Dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / max(1, total),
                "size": len(self._cache),
            }

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 3. Profile Aggregator
#
# Aggregates profiles by service/env for metric computation.
# =============================================================================


class ProfileAggregator:
    """
    Aggregates profiles by service.

    Lock pattern: 1 Lock for all aggregation (simple and efficient)
    """

    def __init__(self, aggregation_window_sec: int = 60):
        self._window = aggregation_window_sec

        # Single lock for all aggregation
        self._lock = threading.Lock()

        self._current_window: int = 0
        self._service_data: Dict[str, Dict] = defaultdict(
            lambda: {"profile_count": 0, "sample_count": 0, "total_cpu_ns": 0, "unique_stacks": set()}
        )

        # Completed aggregations
        self._completed: List[Dict] = []

    def add_profile(self, profile: Profile) -> None:
        """Add a profile to aggregation"""
        window = int(profile.start_time) // self._window

        with self._lock:
            # Check if we need to rotate windows
            if window > self._current_window:
                self._rotate_window(window)

            data = self._service_data[profile.service]
            data["profile_count"] += 1
            data["sample_count"] += len(profile.samples)

            for sample in profile.samples:
                data["total_cpu_ns"] += sample.value
                data["unique_stacks"].add(sample.stack)

    def _rotate_window(self, new_window: int) -> None:
        """Rotate to a new aggregation window (must hold lock)"""
        completed = []
        for service, data in self._service_data.items():
            if data["profile_count"] > 0:
                completed.append(
                    {
                        "service": service,
                        "window": self._current_window,
                        "profile_count": data["profile_count"],
                        "sample_count": data["sample_count"],
                        "total_cpu_ns": data["total_cpu_ns"],
                        "unique_stack_count": len(data["unique_stacks"]),
                    }
                )

            # Reset for new window
            data["profile_count"] = 0
            data["sample_count"] = 0
            data["total_cpu_ns"] = 0
            data["unique_stacks"] = set()

        self._completed.extend(completed)
        self._current_window = new_window

    def get_completed(self) -> List[Dict]:
        """Get and clear completed aggregations"""
        with self._lock:
            result = self._completed
            self._completed = []
            return result

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 4. Profile Ingestion Queue
#
# Queue for incoming profiles with rate limiting.
# =============================================================================


class IngestionQueue:
    """
    Queue for incoming profiles with rate limiting.

    Lock pattern:
    - 1 Condition for queue operations
    - 1 BoundedSemaphore for concurrency limiting
    """

    def __init__(self, max_pending: int = 1000, max_concurrent: int = 10):
        self._max_pending = max_pending

        # Queue with condition
        self._queue: List[Profile] = []
        self._cond = threading.Condition(threading.Lock())

        # Concurrency limiter (BoundedSemaphore)
        self._processing_sem = threading.BoundedSemaphore(value=max_concurrent)

        # Stats
        self._stats_lock = threading.Lock()
        self._ingested = 0
        self._dropped = 0
        self._processed = 0

    def enqueue(self, profile: Profile, timeout: float = 1.0) -> bool:
        """Enqueue a profile for processing"""
        with self._cond:
            if len(self._queue) >= self._max_pending:
                if not self._cond.wait(timeout=timeout):
                    with self._stats_lock:
                        self._dropped += 1
                    return False

            self._queue.append(profile)
            with self._stats_lock:
                self._ingested += 1

            self._cond.notify()
            return True

    def dequeue(self, timeout: float = 1.0) -> Optional[Profile]:
        """Dequeue a profile for processing"""
        with self._cond:
            if not self._queue:
                self._cond.wait(timeout=timeout)

            if not self._queue:
                return None

            profile = self._queue.pop(0)
            self._cond.notify()
            return profile

    def mark_processed(self) -> None:
        """Mark a profile as processed"""
        with self._stats_lock:
            self._processed += 1

    def acquire_processing_slot(self) -> bool:
        """Acquire a slot for processing"""
        return self._processing_sem.acquire(timeout=1.0)

    def release_processing_slot(self) -> None:
        """Release a processing slot"""
        self._processing_sem.release()

    def stats(self) -> Dict:
        with self._stats_lock:
            return {
                "ingested": self._ingested,
                "dropped": self._dropped,
                "processed": self._processed,
                "pending": len(self._queue),
            }

    def count_locks(self) -> int:
        """Count locks"""
        # 1 Condition + 1 BoundedSemaphore + 1 stats lock
        return 3


# =============================================================================
# 5. Profile Processor
# =============================================================================


class ProfileProcessor:
    """
    Processes profiles: resolves symbols, aggregates, exports.

    Realistic lock count breakdown:
    - Symbol cache: 1 RLock
    - Aggregator: 1 Lock
    - Ingestion queue: 3 locks (condition, semaphore, stats)
    - Metrics: 1 Lock

    Total: ~6 base locks
    """

    def __init__(self):
        self.symbol_cache = SymbolCache(max_size=100000)
        self.aggregator = ProfileAggregator(aggregation_window_sec=60)
        self.ingestion_queue = IngestionQueue(max_pending=1000, max_concurrent=10)

        # Processing workers
        self._shutdown = threading.Event()
        self._workers: List[threading.Thread] = []

        # Metrics
        self._metrics_lock = threading.Lock()
        self._profiles_processed = 0
        self._samples_processed = 0

    def start_workers(self, num_workers: int = 4) -> None:
        """Start background processing workers"""
        for i in range(num_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"ProfileWorker-{i}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def _worker_loop(self) -> None:
        """Background worker loop"""
        while not self._shutdown.is_set():
            profile = self.ingestion_queue.dequeue(timeout=0.5)
            if not profile:
                continue

            if not self.ingestion_queue.acquire_processing_slot():
                continue

            try:
                self._process_profile(profile)
                self.ingestion_queue.mark_processed()
            finally:
                self.ingestion_queue.release_processing_slot()

    def _process_profile(self, profile: Profile) -> None:
        """Process a single profile"""
        # Resolve symbols for samples
        for sample in profile.samples:
            for frame in sample.stack:
                symbol_key = f"{frame.filename}:{frame.function}"
                self.symbol_cache.resolve_or_fetch(symbol_key, lambda k: self._resolve_symbol(k))

        # Aggregate
        self.aggregator.add_profile(profile)

        # Update metrics
        with self._metrics_lock:
            self._profiles_processed += 1
            self._samples_processed += len(profile.samples)

    def _resolve_symbol(self, symbol_key: str) -> Dict:
        """Simulate symbol resolution"""
        time.sleep(0.0001)  # Simulate lookup
        return {"demangled": symbol_key.split(":")[-1], "inline": random.random() > 0.8}

    def ingest(self, profile: Profile) -> bool:
        """Ingest a profile for processing"""
        return self.ingestion_queue.enqueue(profile)

    def get_metrics(self) -> Dict:
        with self._metrics_lock:
            return {
                "profiles_processed": self._profiles_processed,
                "samples_processed": self._samples_processed,
                "queue": self.ingestion_queue.stats(),
                "cache": self.symbol_cache.stats(),
            }

    def count_locks(self) -> int:
        """Count total locks"""
        count = 0

        # Symbol cache
        count += self.symbol_cache.count_locks()

        # Aggregator
        count += self.aggregator.count_locks()

        # Ingestion queue
        count += self.ingestion_queue.count_locks()

        # Metrics
        count += 1

        return count

    def shutdown(self) -> None:
        self._shutdown.set()
        for w in self._workers:
            w.join(timeout=2.0)


# =============================================================================
# 6. Traffic Generator
# =============================================================================


def generate_profile(service: str) -> Profile:
    """Generate a random profile"""
    num_samples = random.randint(50, 200)
    samples = []

    for _ in range(num_samples):
        stack_depth = random.randint(5, 15)
        stack = tuple(
            StackFrame(
                function=f"func_{random.randint(1, 50)}",
                filename=f"file_{random.randint(1, 20)}.py",
                lineno=random.randint(1, 500),
            )
            for _ in range(stack_depth)
        )
        samples.append(
            Sample(
                stack=stack, value=random.randint(1000, 1000000), labels={"thread": f"thread_{random.randint(1, 4)}"}
            )
        )

    now = time.time()
    return Profile(
        profile_id=f"profile_{time.time_ns()}_{random.randint(1000, 9999)}",
        service=service,
        env="prod",
        version="1.0.0",
        start_time=now - 60,
        end_time=now,
        samples=samples,
    )


def simulate_traffic(processor: ProfileProcessor, profiles_per_sec: int, duration: int):
    """Simulate incoming profile traffic"""
    logger.info("Simulating %s profiles/sec for %ss", profiles_per_sec, duration)

    services = [f"service_{i}" for i in range(5)]

    def send_profile():
        service = random.choice(services)
        profile = generate_profile(service)
        processor.ingest(profile)

    with ThreadPoolExecutor(max_workers=min(profiles_per_sec * 2, 20)) as executor:
        start = time.time()
        interval = 1.0 / profiles_per_sec

        while time.time() - start < duration:
            executor.submit(send_profile)
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Profiler Backend Lock Usage Simulator")
    parser.add_argument("--pps", type=int, default=50, help="Profiles per second")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--workers", type=int, default=4, help="Processing workers")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("PROFILER BACKEND SIMULATOR - Realistic Lock Usage Patterns")
    logger.info("=" * 60)

    processor = ProfileProcessor()
    processor.start_workers(args.workers)

    try:
        simulate_traffic(processor, args.pps, args.duration)

        # Wait for processing to complete
        time.sleep(2)

        metrics = processor.get_metrics()
        lock_count = processor.count_locks()

        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)
        logger.info("Profiles processed: %s", metrics["profiles_processed"])
        logger.info("Samples processed: %s", metrics["samples_processed"])
        logger.info("Queue stats: %s", metrics["queue"])
        logger.info("Cache stats: %s", metrics["cache"])
        logger.info("Total locks: %s", lock_count)
        logger.info("=" * 60)

    finally:
        processor.shutdown()


if __name__ == "__main__":
    main()
