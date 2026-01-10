#!/usr/bin/env python3
"""
Logs Pipeline Simulator - Simulates Datadog's log processing service

This simulates the lock usage patterns of a high-throughput log processing pipeline:
- Log intake with batching
- Parser pool for structured log parsing
- Index routing with rate limiting
- Archive writer coordination
- Pipeline metrics aggregation

Realistic lock counts: 20-40 locks per process
- Based on typical streaming/pipeline architectures:
  - Intake buffers: 1 Condition each
  - Parser pool: 1 Lock + 1 Semaphore
  - Router: 1 Lock per destination (few destinations)
  - Archive writer: 1 Lock
  - Metrics: 1 Lock

Contention level: High (streaming data with backpressure)
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
from typing import Dict
from typing import List


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# 1. Log Event Data Structure
# =============================================================================


@dataclass
class LogEvent:
    """Represents a log event"""

    timestamp: float
    source: str
    service: str
    host: str
    message: str
    tags: Dict[str, str] = field(default_factory=dict)
    parsed_attributes: Dict[str, str] = field(default_factory=dict)


# =============================================================================
# 2. Intake Buffer (Batched ingestion with backpressure)
#
# Based on typical streaming patterns - uses Condition for producer/consumer.
# =============================================================================


class IntakeBuffer:
    """
    Buffer for incoming logs with backpressure support.

    Lock pattern: 1 Condition for producer/consumer synchronization.
    """

    def __init__(self, name: str, max_size: int = 10000, batch_size: int = 100):
        self.name = name
        self._max_size = max_size
        self._batch_size = batch_size
        self._buffer: List[LogEvent] = []

        # Single condition for the buffer
        self._cond = threading.Condition(threading.Lock())

        # Stats
        self._stats_lock = threading.Lock()
        self._received = 0
        self._dropped = 0
        self._batches_sent = 0

    def put(self, event: LogEvent, timeout: float = 0.1) -> bool:
        """Add a log event to the buffer"""
        with self._cond:
            if len(self._buffer) >= self._max_size:
                # Apply backpressure
                if not self._cond.wait(timeout=timeout):
                    with self._stats_lock:
                        self._dropped += 1
                    return False

            self._buffer.append(event)
            with self._stats_lock:
                self._received += 1

            # Notify if batch ready
            if len(self._buffer) >= self._batch_size:
                self._cond.notify()
            return True

    def get_batch(self, timeout: float = 1.0) -> List[LogEvent]:
        """Get a batch of log events"""
        with self._cond:
            # Wait for batch to be ready
            while len(self._buffer) < self._batch_size:
                if not self._cond.wait(timeout=timeout):
                    # Timeout - return partial batch
                    break

            if not self._buffer:
                return []

            batch = self._buffer[: self._batch_size]
            self._buffer = self._buffer[self._batch_size :]

            with self._stats_lock:
                self._batches_sent += 1

            self._cond.notify_all()  # Notify producers
            return batch

    def stats(self) -> Dict:
        with self._stats_lock:
            return {
                "received": self._received,
                "dropped": self._dropped,
                "batches_sent": self._batches_sent,
                "pending": len(self._buffer),
            }

    def count_locks(self) -> int:
        # 1 Condition + 1 stats lock
        return 2


# =============================================================================
# 3. Parser Pool
#
# Manages a pool of parsers with concurrency limiting.
# =============================================================================


class ParserPool:
    """
    Pool of log parsers with concurrency limiting.

    Lock pattern: 1 Semaphore for concurrency, 1 Lock for metrics.
    Real parsers are stateless, so no per-parser locks needed.
    """

    def __init__(self, max_concurrent: int = 10):
        self._max_concurrent = max_concurrent
        self._semaphore = threading.Semaphore(value=max_concurrent)

        # Parser patterns (simulated)
        self._patterns = [
            ("apache", r"(?P<ip>\d+\.\d+\.\d+\.\d+).*"),
            ("json", r"\{.*\}"),
            ("syslog", r"<\d+>.*"),
        ]

        # Stats
        self._stats_lock = threading.Lock()
        self._parsed = 0
        self._errors = 0

    def parse(self, event: LogEvent) -> LogEvent:
        """Parse a log event (with concurrency limiting)"""
        if not self._semaphore.acquire(timeout=1.0):
            return event  # Skip parsing if overloaded

        try:
            # Simulate parsing work
            time.sleep(random.uniform(0.0001, 0.0005))

            # Add some parsed attributes
            event.parsed_attributes = {
                "level": random.choice(["INFO", "WARN", "ERROR", "DEBUG"]),
                "parsed_at": str(time.time()),
            }

            with self._stats_lock:
                self._parsed += 1

            return event
        except Exception:
            with self._stats_lock:
                self._errors += 1
            return event
        finally:
            self._semaphore.release()

    def stats(self) -> Dict:
        with self._stats_lock:
            return {"parsed": self._parsed, "errors": self._errors}

    def count_locks(self) -> int:
        # 1 Semaphore + 1 stats lock
        return 2


# =============================================================================
# 4. Index Router
#
# Routes logs to different indexes based on rules.
# =============================================================================


class IndexRouter:
    """
    Routes logs to different indexes.

    Lock pattern: 1 Lock for routing rules + 1 Lock per destination counter.
    In practice, destinations are few (3-10), not per-customer.
    """

    def __init__(self, num_indexes: int = 5):
        self._indexes = [f"index_{i}" for i in range(num_indexes)]

        # Routing rules (read-mostly, rarely updated)
        self._rules_lock = threading.RLock()
        self._rules: Dict[str, str] = {}  # service -> index

        # Per-index stats (single lock for all, not per-index)
        self._stats_lock = threading.Lock()
        self._index_counts: Dict[str, int] = defaultdict(int)

    def route(self, event: LogEvent) -> str:
        """Determine which index to route the log to"""
        # Check rules (read lock)
        with self._rules_lock:
            if event.service in self._rules:
                index = self._rules[event.service]
            else:
                # Default routing by hash
                index = self._indexes[hash(event.service) % len(self._indexes)]

        # Update stats
        with self._stats_lock:
            self._index_counts[index] += 1

        return index

    def add_rule(self, service: str, index: str) -> None:
        """Add a routing rule"""
        with self._rules_lock:
            self._rules[service] = index

    def stats(self) -> Dict:
        with self._stats_lock:
            return dict(self._index_counts)

    def count_locks(self) -> int:
        # 1 RLock (rules) + 1 Lock (stats)
        return 2


# =============================================================================
# 5. Archive Writer
#
# Writes logs to cold storage in batches.
# =============================================================================


class ArchiveWriter:
    """
    Writes logs to archive storage.

    Lock pattern: 1 Lock for buffer + 1 Condition for flush coordination.
    """

    def __init__(self, flush_interval: float = 5.0, batch_size: int = 1000):
        self._flush_interval = flush_interval
        self._batch_size = batch_size

        self._buffer: List[LogEvent] = []
        self._cond = threading.Condition(threading.Lock())

        # Stats
        self._stats_lock = threading.Lock()
        self._archived = 0
        self._flush_count = 0

        # Background flusher
        self._shutdown = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True, name="ArchiveWriter")
        self._flusher.start()

    def write(self, event: LogEvent) -> None:
        """Add event to archive buffer"""
        with self._cond:
            self._buffer.append(event)
            if len(self._buffer) >= self._batch_size:
                self._cond.notify()

    def _flush_loop(self) -> None:
        """Background flush loop"""
        while not self._shutdown.is_set():
            with self._cond:
                self._cond.wait(timeout=self._flush_interval)
                if self._buffer:
                    batch = self._buffer[:]
                    self._buffer = []
                else:
                    batch = []

            if batch:
                # Simulate archive write
                time.sleep(0.01)
                with self._stats_lock:
                    self._archived += len(batch)
                    self._flush_count += 1

    def stats(self) -> Dict:
        with self._stats_lock:
            return {"archived": self._archived, "flush_count": self._flush_count}

    def count_locks(self) -> int:
        # 1 Condition + 1 stats lock
        return 2

    def shutdown(self) -> None:
        self._shutdown.set()
        with self._cond:
            self._cond.notify()
        self._flusher.join(timeout=5.0)


# =============================================================================
# 6. Rate Limiter (per-source)
#
# Limits log ingestion rate per source.
# =============================================================================


class SourceRateLimiter:
    """
    Rate limiter with per-source tracking.

    Lock pattern: 1 Lock for all rate tracking.
    We use a single lock, not per-source locks, because:
    1. The operation is fast (just counter updates)
    2. Per-source locks would create thousands of locks
    """

    def __init__(self, default_rate: int = 1000):
        self._default_rate = default_rate  # events per second
        self._lock = threading.Lock()
        self._source_counts: Dict[str, int] = defaultdict(int)
        self._source_timestamps: Dict[str, float] = {}
        self._dropped: Dict[str, int] = defaultdict(int)

    def allow(self, source: str) -> bool:
        """Check if an event from this source should be allowed"""
        now = time.time()

        with self._lock:
            last_ts = self._source_timestamps.get(source, 0)

            # Reset counter if new second
            if now - last_ts >= 1.0:
                self._source_counts[source] = 0
                self._source_timestamps[source] = now

            if self._source_counts[source] >= self._default_rate:
                self._dropped[source] += 1
                return False

            self._source_counts[source] += 1
            return True

    def stats(self) -> Dict:
        with self._lock:
            return {"dropped_by_source": dict(self._dropped)}

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 7. Pipeline Metrics
#
# Aggregates pipeline metrics.
# =============================================================================


class PipelineMetrics:
    """
    Aggregates pipeline metrics.

    Lock pattern: 1 Lock for atomic metric updates.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events_processed = 0
        self._bytes_processed = 0
        self._latency_sum = 0.0
        self._latency_count = 0

    def record_event(self, event: LogEvent, latency: float) -> None:
        """Record metrics for a processed event"""
        with self._lock:
            self._events_processed += 1
            self._bytes_processed += len(event.message)
            self._latency_sum += latency
            self._latency_count += 1

    def get_metrics(self) -> Dict:
        with self._lock:
            avg_latency = self._latency_sum / max(1, self._latency_count)
            return {
                "events_processed": self._events_processed,
                "bytes_processed": self._bytes_processed,
                "avg_latency_ms": avg_latency * 1000,
            }

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 8. Logs Pipeline Simulator
# =============================================================================


class LogsPipelineSimulator:
    """
    Main simulator for logs pipeline.

    Realistic lock count breakdown:
    - Intake buffers: 2 locks per buffer (3 buffers = 6)
    - Parser pool: 2 locks
    - Index router: 2 locks
    - Archive writer: 2 locks
    - Rate limiter: 1 lock
    - Metrics: 1 lock

    Total: ~14-20 locks
    """

    def __init__(
        self,
        num_intake_buffers: int = 3,
        parser_concurrency: int = 10,
        num_indexes: int = 5,
    ):
        # Intake buffers (one per data source type)
        self.intake_buffers = [
            IntakeBuffer(name=f"intake_{i}", max_size=10000, batch_size=100) for i in range(num_intake_buffers)
        ]

        # Parser pool
        self.parser_pool = ParserPool(max_concurrent=parser_concurrency)

        # Index router
        self.router = IndexRouter(num_indexes=num_indexes)

        # Archive writer
        self.archive_writer = ArchiveWriter(flush_interval=5.0, batch_size=1000)

        # Rate limiter
        self.rate_limiter = SourceRateLimiter(default_rate=1000)

        # Metrics
        self.metrics = PipelineMetrics()

        # Processing workers
        self._shutdown = threading.Event()
        self._workers: List[threading.Thread] = []

        logger.info(
            "LogsPipelineSimulator initialized with %s intake buffers, %s indexes",
            num_intake_buffers,
            num_indexes,
        )

    def ingest(self, event: LogEvent) -> bool:
        """Ingest a log event"""
        # Rate limit check
        if not self.rate_limiter.allow(event.source):
            return False

        # Route to intake buffer
        buffer_idx = hash(event.source) % len(self.intake_buffers)
        return self.intake_buffers[buffer_idx].put(event)

    def start_workers(self, num_workers: int = 4) -> None:
        """Start pipeline workers"""
        for i in range(num_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"PipelineWorker-{i}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def _worker_loop(self) -> None:
        """Pipeline worker loop"""
        while not self._shutdown.is_set():
            # Get batch from any intake buffer
            for buffer in self.intake_buffers:
                batch = buffer.get_batch(timeout=0.5)
                if batch:
                    self._process_batch(batch)
                    break

    def _process_batch(self, batch: List[LogEvent]) -> None:
        """Process a batch of log events"""
        for event in batch:
            start_time = time.time()

            # Parse
            event = self.parser_pool.parse(event)

            # Route to index
            self.router.route(event)

            # Archive
            self.archive_writer.write(event)

            # Record metrics
            latency = time.time() - start_time
            self.metrics.record_event(event, latency)

    def get_stats(self) -> Dict:
        """Get pipeline stats"""
        return {
            "metrics": self.metrics.get_metrics(),
            "parser": self.parser_pool.stats(),
            "router": self.router.stats(),
            "archive": self.archive_writer.stats(),
            "rate_limiter": self.rate_limiter.stats(),
        }

    def count_locks(self) -> int:
        """Count total locks"""
        count = 0

        # Intake buffers
        for buffer in self.intake_buffers:
            count += buffer.count_locks()

        # Parser pool
        count += self.parser_pool.count_locks()

        # Router
        count += self.router.count_locks()

        # Archive writer
        count += self.archive_writer.count_locks()

        # Rate limiter
        count += self.rate_limiter.count_locks()

        # Metrics
        count += self.metrics.count_locks()

        return count

    def shutdown(self) -> None:
        self._shutdown.set()
        for w in self._workers:
            w.join(timeout=2.0)
        self.archive_writer.shutdown()


# =============================================================================
# Traffic Generator
# =============================================================================


def generate_log_event(sources: List[str], services: List[str]) -> LogEvent:
    """Generate a random log event"""
    return LogEvent(
        timestamp=time.time(),
        source=random.choice(sources),
        service=random.choice(services),
        host=f"host-{random.randint(1, 100)}",
        message=f"Log message {random.randint(1, 10000)} - " + "x" * random.randint(50, 500),
        tags={"env": random.choice(["prod", "staging", "dev"])},
    )


def simulate_traffic(pipeline: LogsPipelineSimulator, events_per_second: int, duration: int):
    """Simulate incoming log traffic"""
    logger.info("Simulating %s events/sec for %ss", events_per_second, duration)

    sources = [f"source_{i}" for i in range(10)]
    services = [f"service_{i}" for i in range(20)]

    def send_event():
        event = generate_log_event(sources, services)
        pipeline.ingest(event)

    with ThreadPoolExecutor(max_workers=min(events_per_second // 10 + 1, 50)) as executor:
        start = time.time()
        interval = 1.0 / events_per_second

        while time.time() - start < duration:
            executor.submit(send_event)
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Logs Pipeline Lock Usage Simulator")
    parser.add_argument("--eps", type=int, default=1000, help="Events per second")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--workers", type=int, default=4, help="Pipeline workers")
    parser.add_argument("--buffers", type=int, default=3, help="Intake buffers")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("LOGS PIPELINE SIMULATOR - Realistic Lock Usage Patterns")
    logger.info("=" * 60)

    pipeline = LogsPipelineSimulator(
        num_intake_buffers=args.buffers,
        parser_concurrency=10,
        num_indexes=5,
    )
    pipeline.start_workers(args.workers)

    try:
        simulate_traffic(pipeline, args.eps, args.duration)

        # Wait for processing
        time.sleep(2)

        stats = pipeline.get_stats()
        lock_count = pipeline.count_locks()

        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)
        logger.info("Events processed: %s", stats["metrics"]["events_processed"])
        logger.info("Bytes processed: %s", stats["metrics"]["bytes_processed"])
        logger.info("Avg latency: %.2f ms", stats["metrics"]["avg_latency_ms"])
        logger.info("Parser stats: %s", stats["parser"])
        logger.info("Archive stats: %s", stats["archive"])
        logger.info("Total locks: %s", lock_count)
        logger.info("=" * 60)

    finally:
        pipeline.shutdown()


if __name__ == "__main__":
    main()
