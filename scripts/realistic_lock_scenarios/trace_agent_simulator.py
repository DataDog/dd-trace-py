#!/usr/bin/env python3
"""
Trace Agent Simulator - Simulates Datadog's trace intake/processing service

This simulates the lock usage patterns of a trace processing service:
- Span buffer management (ring buffers with conditions)
- Concurrent trace writers
- Sampling decision coordination
- Periodic flush operations to backend
- Stats aggregation

Realistic lock counts: 30-80 locks per process
- Based on analysis of dd-trace-py's internal writer:
  - Writer: 1 RLock + 1 Condition
  - Encoder: 1-2 RLocks
  - Stats processor: 1 Lock
  - Per-service buckets: ~1 lock each

Contention level: Medium-High (concurrent span writes)
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
from typing import Optional
from typing import Tuple


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# 1. Span Buffer (Ring buffer with Condition)
#
# Based on dd-trace-py's internal buffer patterns.
# Uses 1 Condition for the entire buffer (not per-slot locks).
# =============================================================================


@dataclass
class Span:
    """Represents a trace span"""

    trace_id: int
    span_id: int
    parent_id: Optional[int]
    service: str
    name: str
    resource: str
    start_ns: int
    duration_ns: int
    meta: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)


class SpanBuffer:
    """
    Thread-safe ring buffer for spans.

    Lock pattern: 1 Condition (wrapping 1 Lock) for the entire buffer.
    This is similar to dd-trace-py's span buffer implementation.
    """

    def __init__(self, name: str, capacity: int = 10000):
        self.name = name
        self._capacity = capacity
        self._buffer: List[Optional[Span]] = [None] * capacity
        self._write_idx = 0
        self._read_idx = 0
        self._count = 0

        # Single condition for the buffer (like dd-trace-py's writer)
        self._cond = threading.Condition(threading.Lock())

        # Stats (separate lock)
        self._stats_lock = threading.Lock()
        self._total_written = 0
        self._total_read = 0
        self._dropped = 0

    def write(self, span: Span, timeout: float = 0.1) -> bool:
        """Write a span to the buffer"""
        with self._cond:
            if self._count >= self._capacity:
                # Buffer full - wait or drop
                if not self._cond.wait(timeout=timeout):
                    with self._stats_lock:
                        self._dropped += 1
                    return False

            self._buffer[self._write_idx] = span
            self._write_idx = (self._write_idx + 1) % self._capacity
            self._count += 1

            with self._stats_lock:
                self._total_written += 1

            self._cond.notify()
            return True

    def read_batch(self, max_count: int, timeout: float = 0.5) -> List[Span]:
        """Read a batch of spans from the buffer"""
        with self._cond:
            if self._count == 0:
                self._cond.wait(timeout=timeout)

            if self._count == 0:
                return []

            batch_size = min(max_count, self._count)
            batch = []

            for _ in range(batch_size):
                span = self._buffer[self._read_idx]
                self._buffer[self._read_idx] = None
                self._read_idx = (self._read_idx + 1) % self._capacity
                self._count -= 1
                if span:
                    batch.append(span)

            with self._stats_lock:
                self._total_read += len(batch)

            self._cond.notify_all()
            return batch

    def stats(self) -> Dict:
        with self._stats_lock:
            return {
                "written": self._total_written,
                "read": self._total_read,
                "dropped": self._dropped,
                "current_count": self._count,
            }

    def count_locks(self) -> int:
        """Count locks in this buffer"""
        # 1 Condition + 1 stats lock
        return 2


# =============================================================================
# 2. Sampling Coordinator
#
# Based on dd-trace-py's sampling logic.
# Uses 1 Lock for rate tracking.
# =============================================================================


class SamplingCoordinator:
    """
    Coordinates sampling decisions across services.

    Lock pattern: 1 Lock for atomic rate updates (like dd-trace-py's RateSampler)
    """

    def __init__(self, default_rate: float = 0.1):
        self._default_rate = default_rate

        # Single lock for all sampling state (like dd-trace-py)
        self._lock = threading.Lock()
        self._service_rates: Dict[str, float] = {}
        self._counts: Dict[str, int] = defaultdict(int)
        self._sampled: Dict[str, int] = defaultdict(int)

    def should_sample(self, service: str, trace_id: int) -> Tuple[bool, int]:
        """
        Decide if a trace should be sampled.
        Returns (should_sample, priority)
        """
        with self._lock:
            rate = self._service_rates.get(service, self._default_rate)
            self._counts[service] += 1

            # Deterministic sampling based on trace_id
            sample = (trace_id % 1000) < (rate * 1000)
            priority = 1 if sample else 0

            if sample:
                self._sampled[service] += 1

            return sample, priority

    def set_rate(self, service: str, rate: float):
        """Update sampling rate for a service"""
        with self._lock:
            self._service_rates[service] = max(0.0, min(1.0, rate))

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 3. Stats Aggregator
#
# Aggregates trace statistics like dd-trace-py's stats processor.
# =============================================================================


class StatsAggregator:
    """
    Aggregates trace statistics.

    Lock pattern: 1 Lock for stats bucket updates (like dd-trace-py's StatsProcessor)
    """

    def __init__(self, bucket_duration_sec: int = 10):
        self._bucket_duration = bucket_duration_sec

        # Single lock for all stats (like dd-trace-py)
        self._lock = threading.Lock()
        self._buckets: Dict[int, Dict[str, int]] = {}
        self._service_stats: Dict[str, Dict] = defaultdict(lambda: {"hits": 0, "errors": 0, "duration_sum": 0})

    def record_span(self, span: Span):
        """Record span statistics"""
        bucket_id = int(span.start_ns / 1e9) // self._bucket_duration

        with self._lock:
            if bucket_id not in self._buckets:
                self._buckets[bucket_id] = {"span_count": 0}
            self._buckets[bucket_id]["span_count"] += 1

            stats = self._service_stats[span.service]
            stats["hits"] += 1
            stats["duration_sum"] += span.duration_ns
            if span.meta.get("error") == "true":
                stats["errors"] += 1

    def flush_bucket(self, bucket_id: int) -> Optional[Dict]:
        """Flush a completed bucket"""
        with self._lock:
            return self._buckets.pop(bucket_id, None)

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 4. Trace Writer
#
# Writes traces to backend, similar to dd-trace-py's AgentWriter.
# =============================================================================


class TraceWriter:
    """
    Writes traces to backend in batches.

    Lock pattern: 1 RLock for connection + 1 Condition for flush coordination
    (Based on dd-trace-py's AgentWriter)
    """

    def __init__(self, name: str, flush_interval: float = 1.0, batch_size: int = 1000):
        self.name = name
        self._flush_interval = flush_interval
        self._batch_size = batch_size

        # Connection lock (RLock like dd-trace-py)
        self._conn_lock = threading.RLock()

        # Pending traces
        self._pending: List[Span] = []
        self._pending_lock = threading.Lock()

        # Flush coordination
        self._flush_cv = threading.Condition()

        # Stats
        self._stats_lock = threading.Lock()
        self._total_flushed = 0
        self._flush_count = 0

        # Background flusher
        self._shutdown = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, name=f"Writer-{name}", daemon=True)
        self._flusher.start()

    def add_spans(self, spans: List[Span]):
        """Add spans to pending buffer"""
        with self._pending_lock:
            self._pending.extend(spans)

            # Trigger flush if batch is full
            if len(self._pending) >= self._batch_size:
                with self._flush_cv:
                    self._flush_cv.notify()

    def _flush_loop(self):
        """Background flush loop"""
        while not self._shutdown.is_set():
            with self._flush_cv:
                self._flush_cv.wait(timeout=self._flush_interval)

            self._do_flush()

    def _do_flush(self):
        """Flush pending spans to backend"""
        with self._pending_lock:
            if not self._pending:
                return

            batch = self._pending[: self._batch_size]
            self._pending = self._pending[self._batch_size :]

        # Simulate network write with connection lock
        with self._conn_lock:
            time.sleep(0.005 * len(batch) / 100)  # Simulate network

            with self._stats_lock:
                self._total_flushed += len(batch)
                self._flush_count += 1

    def stats(self) -> Dict:
        with self._stats_lock:
            return {"flushed": self._total_flushed, "flush_count": self._flush_count}

    def count_locks(self) -> int:
        """Count locks in this writer"""
        # 1 RLock (conn) + 1 Lock (pending) + 1 Condition (flush) + 1 Lock (stats)
        return 4

    def shutdown(self):
        self._shutdown.set()
        with self._flush_cv:
            self._flush_cv.notify()
        self._flusher.join(timeout=5.0)
        self._do_flush()  # Final flush


# =============================================================================
# 5. Trace Agent Simulator
# =============================================================================


class TraceAgentSimulator:
    """
    Main simulator orchestrating all trace agent components.

    Realistic lock count breakdown:
    - Span buffers: 2 locks per buffer (10 buffers = 20 locks)
    - Sampler: 1 lock
    - Stats aggregator: 1 lock
    - Writers: 4 locks per writer (2 writers = 8 locks)
    - Metrics: 1 lock
    - Buffers management: 1 lock

    Total: ~32 locks (not 500-2,000!)
    """

    def __init__(self, num_buffers: int = 10, buffer_capacity: int = 10000, num_writers: int = 2):
        # Multiple span buffers (per-endpoint)
        self.buffers: Dict[str, SpanBuffer] = {}
        self._buffers_lock = threading.Lock()
        for i in range(num_buffers):
            name = f"buffer_{i}"
            self.buffers[name] = SpanBuffer(name=name, capacity=buffer_capacity)

        # Sampling coordinator
        self.sampler = SamplingCoordinator(default_rate=0.1)

        # Stats aggregator
        self.stats_agg = StatsAggregator()

        # Writers
        self.writers = [
            TraceWriter(name=f"writer_{i}", flush_interval=1.0, batch_size=1000) for i in range(num_writers)
        ]

        # Metrics
        self._metrics_lock = threading.Lock()
        self._spans_received = 0
        self._spans_sampled = 0

        # Processing workers
        self._shutdown = threading.Event()
        self._processors: List[threading.Thread] = []

        logger.info("TraceAgentSimulator initialized with %s buffers, %s writers", num_buffers, num_writers)

    def _get_buffer(self, service: str) -> SpanBuffer:
        """Get buffer for a service (round-robin)"""
        buffer_names = list(self.buffers.keys())
        idx = hash(service) % len(buffer_names)
        return self.buffers[buffer_names[idx]]

    def receive_span(self, span: Span) -> bool:
        """
        Receive a span from a tracer.
        This is the hot path with lock contention.
        """
        with self._metrics_lock:
            self._spans_received += 1

        # Sampling decision
        should_sample, priority = self.sampler.should_sample(span.service, span.trace_id)

        if not should_sample:
            return False

        with self._metrics_lock:
            self._spans_sampled += 1

        # Add to buffer
        buffer = self._get_buffer(span.service)
        if not buffer.write(span):
            return False

        # Record stats
        self.stats_agg.record_span(span)

        return True

    def start_processors(self, num_processors: int = 4):
        """Start background processors that drain buffers"""
        for i in range(num_processors):
            t = threading.Thread(target=self._processor_loop, name=f"Processor-{i}", daemon=True)
            t.start()
            self._processors.append(t)

    def _processor_loop(self):
        """Background processor loop"""
        while not self._shutdown.is_set():
            for name, buffer in list(self.buffers.items()):
                batch = buffer.read_batch(max_count=100, timeout=0.1)
                if batch:
                    # Send to writers (round-robin)
                    writer = random.choice(self.writers)
                    writer.add_spans(batch)

    def get_metrics(self) -> Dict:
        with self._metrics_lock:
            return {"spans_received": self._spans_received, "spans_sampled": self._spans_sampled}

    def count_locks(self) -> int:
        """Count total locks in the system"""
        count = 0

        # Buffers management lock
        count += 1

        # Per-buffer locks
        for buffer in self.buffers.values():
            count += buffer.count_locks()

        # Sampler
        count += self.sampler.count_locks()

        # Stats aggregator
        count += self.stats_agg.count_locks()

        # Writers
        for writer in self.writers:
            count += writer.count_locks()

        # Metrics
        count += 1

        return count

    def shutdown(self):
        self._shutdown.set()
        for p in self._processors:
            p.join(timeout=2.0)
        for w in self.writers:
            w.shutdown()


# =============================================================================
# Traffic Generator
# =============================================================================


def generate_span(service: str) -> Span:
    """Generate a random span"""
    now = time.time_ns()
    return Span(
        trace_id=random.randint(1, 2**63),
        span_id=random.randint(1, 2**63),
        parent_id=random.randint(1, 2**63) if random.random() > 0.3 else None,
        service=service,
        name=random.choice(["http.request", "db.query", "cache.get", "rpc.call"]),
        resource=random.choice(["/api/v1/foo", "/api/v1/bar", "SELECT *", "GET key"]),
        start_ns=now - random.randint(1_000_000, 100_000_000),
        duration_ns=random.randint(100_000, 10_000_000),
        meta={"env": "prod", "version": "1.0"} if random.random() > 0.5 else {},
        metrics={"_sample_rate": 0.1} if random.random() > 0.5 else {},
    )


def simulate_traffic(agent: TraceAgentSimulator, spans_per_second: int, duration: int):
    """Simulate incoming trace traffic"""
    logger.info("Simulating %s spans/sec for %ss", spans_per_second, duration)

    services = [f"service_{i}" for i in range(10)]

    def send_span():
        service = random.choice(services)
        span = generate_span(service)
        agent.receive_span(span)

    with ThreadPoolExecutor(max_workers=min(spans_per_second // 10 + 1, 50)) as executor:
        start = time.time()
        interval = 1.0 / spans_per_second

        while time.time() - start < duration:
            executor.submit(send_span)
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Trace Agent Lock Usage Simulator")
    parser.add_argument("--sps", type=int, default=1000, help="Spans per second")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--buffers", type=int, default=10, help="Number of span buffers")
    parser.add_argument("--processors", type=int, default=4, help="Number of processors")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("TRACE AGENT SIMULATOR - Realistic Lock Usage Patterns")
    logger.info("=" * 60)

    agent = TraceAgentSimulator(num_buffers=args.buffers, buffer_capacity=10000, num_writers=2)
    agent.start_processors(args.processors)

    try:
        simulate_traffic(agent, args.sps, args.duration)

        metrics = agent.get_metrics()
        lock_count = agent.count_locks()

        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)
        logger.info("Spans received: %s", metrics["spans_received"])
        logger.info("Spans sampled: %s", metrics["spans_sampled"])
        sample_rate = metrics["spans_sampled"] / max(1, metrics["spans_received"]) * 100
        logger.info("Sample rate: %.1f%%", sample_rate)
        logger.info("Total locks: %s", lock_count)
        logger.info("=" * 60)

    finally:
        agent.shutdown()


if __name__ == "__main__":
    main()
