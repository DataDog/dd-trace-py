#!/usr/bin/env python3
"""
Agent Checks Simulator - Simulates the Python parts of the Datadog Agent

This simulates the lock usage patterns of the Datadog Agent's check system:
- Check scheduler with periodic execution
- Collector running checks in parallel
- Aggregator for metrics/events/service checks
- Forwarder for sending data to intake

Realistic lock counts: 15-30 locks per process
- Based on typical agent architecture:
  - Scheduler: 1 Lock + 1 Condition
  - Collector: 1 Lock + 1 Semaphore per check type
  - Aggregator: 1 Lock per data type (metrics, events, service_checks)
  - Forwarder: 1 Lock + 1 Condition

Contention level: Low-Medium (checks run on intervals, not continuous)
"""

import argparse
from dataclasses import dataclass
from dataclasses import field
import logging
import random
import threading
import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# 1. Data Structures
# =============================================================================


@dataclass
class Metric:
    """Represents a metric point"""

    name: str
    value: float
    tags: List[str]
    timestamp: float
    metric_type: str = "gauge"  # gauge, count, rate


@dataclass
class Event:
    """Represents an event"""

    title: str
    text: str
    tags: List[str]
    timestamp: float
    priority: str = "normal"


@dataclass
class ServiceCheck:
    """Represents a service check result"""

    name: str
    status: int  # 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN
    tags: List[str]
    timestamp: float
    message: str = ""


@dataclass
class CheckInstance:
    """Represents a check instance configuration"""

    check_name: str
    instance_id: str
    interval: float
    config: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# 2. Check Scheduler
#
# Manages when checks should run.
# =============================================================================


class CheckScheduler:
    """
    Schedules check execution based on intervals.

    Lock pattern: 1 Lock for schedule state + 1 Condition for wakeup.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._schedule: Dict[str, float] = {}  # instance_id -> next_run_time
        self._intervals: Dict[str, float] = {}  # instance_id -> interval
        self._instances: Dict[str, CheckInstance] = {}

    def register(self, instance: CheckInstance) -> None:
        """Register a check instance"""
        with self._lock:
            self._instances[instance.instance_id] = instance
            self._intervals[instance.instance_id] = instance.interval
            self._schedule[instance.instance_id] = time.time()  # Run immediately
            self._cond.notify()

    def get_due_checks(self) -> List[CheckInstance]:
        """Get checks that are due to run"""
        now = time.time()
        due = []

        with self._lock:
            for instance_id, next_run in list(self._schedule.items()):
                if next_run <= now:
                    due.append(self._instances[instance_id])
                    # Schedule next run
                    self._schedule[instance_id] = now + self._intervals[instance_id]

        return due

    def wait_for_next(self, timeout: float = 1.0) -> Optional[float]:
        """Wait until next check is due, return time until next check"""
        with self._cond:
            if not self._schedule:
                self._cond.wait(timeout=timeout)
                return None

            next_time = min(self._schedule.values())
            wait_time = max(0, next_time - time.time())
            if wait_time > 0:
                self._cond.wait(timeout=min(wait_time, timeout))
            return wait_time

    def count_locks(self) -> int:
        # 1 Condition (wraps Lock)
        return 1


# =============================================================================
# 3. Collector
#
# Runs checks in parallel with concurrency limiting.
# =============================================================================


class Collector:
    """
    Runs checks and collects results.

    Lock pattern:
    - 1 Semaphore for overall concurrency limiting
    - 1 Lock for results tracking
    """

    def __init__(self, max_concurrent: int = 10):
        self._semaphore = threading.Semaphore(value=max_concurrent)
        self._results_lock = threading.Lock()
        self._check_runs = 0
        self._check_errors = 0
        self._last_run_times: Dict[str, float] = {}

    def run_check(
        self,
        instance: CheckInstance,
        check_func: Callable[[CheckInstance], tuple],
    ) -> Optional[tuple]:
        """Run a check with concurrency limiting"""
        if not self._semaphore.acquire(timeout=5.0):
            return None

        try:
            start_time = time.time()
            result = check_func(instance)
            elapsed = time.time() - start_time

            with self._results_lock:
                self._check_runs += 1
                self._last_run_times[instance.instance_id] = elapsed

            return result
        except Exception as e:
            with self._results_lock:
                self._check_errors += 1
            logger.debug("Check %s failed: %s", instance.check_name, e)
            return None
        finally:
            self._semaphore.release()

    def stats(self) -> Dict:
        with self._results_lock:
            return {
                "check_runs": self._check_runs,
                "check_errors": self._check_errors,
                "active_checks": len(self._last_run_times),
            }

    def count_locks(self) -> int:
        # 1 Semaphore + 1 Lock
        return 2


# =============================================================================
# 4. Aggregator
#
# Aggregates metrics, events, and service checks.
# =============================================================================


class Aggregator:
    """
    Aggregates check data for submission.

    Lock pattern:
    - 1 Lock for metrics buffer
    - 1 Lock for events buffer
    - 1 Lock for service checks buffer

    Could use single lock, but separate locks reduce contention.
    """

    def __init__(self, flush_interval: float = 15.0):
        self._flush_interval = flush_interval

        # Separate buffers with separate locks
        self._metrics: List[Metric] = []
        self._metrics_lock = threading.Lock()

        self._events: List[Event] = []
        self._events_lock = threading.Lock()

        self._service_checks: List[ServiceCheck] = []
        self._service_checks_lock = threading.Lock()

        # Stats
        self._stats_lock = threading.Lock()
        self._metrics_count = 0
        self._events_count = 0
        self._service_checks_count = 0

    def add_metrics(self, metrics: List[Metric]) -> None:
        """Add metrics to buffer"""
        with self._metrics_lock:
            self._metrics.extend(metrics)
        with self._stats_lock:
            self._metrics_count += len(metrics)

    def add_event(self, event: Event) -> None:
        """Add event to buffer"""
        with self._events_lock:
            self._events.append(event)
        with self._stats_lock:
            self._events_count += 1

    def add_service_check(self, check: ServiceCheck) -> None:
        """Add service check to buffer"""
        with self._service_checks_lock:
            self._service_checks.append(check)
        with self._stats_lock:
            self._service_checks_count += 1

    def flush(self) -> tuple:
        """Flush all buffers and return data"""
        with self._metrics_lock:
            metrics = self._metrics[:]
            self._metrics = []

        with self._events_lock:
            events = self._events[:]
            self._events = []

        with self._service_checks_lock:
            service_checks = self._service_checks[:]
            self._service_checks = []

        return metrics, events, service_checks

    def stats(self) -> Dict:
        with self._stats_lock:
            return {
                "metrics_count": self._metrics_count,
                "events_count": self._events_count,
                "service_checks_count": self._service_checks_count,
            }

    def count_locks(self) -> int:
        # 3 buffer locks + 1 stats lock
        return 4


# =============================================================================
# 5. Forwarder
#
# Sends data to Datadog intake.
# =============================================================================


class Forwarder:
    """
    Forwards data to intake endpoint.

    Lock pattern:
    - 1 Lock for queue
    - 1 Condition for flush coordination
    """

    def __init__(self, flush_interval: float = 10.0):
        self._flush_interval = flush_interval

        self._queue: List[tuple] = []
        self._cond = threading.Condition(threading.Lock())

        # Stats
        self._stats_lock = threading.Lock()
        self._payloads_sent = 0
        self._bytes_sent = 0
        self._errors = 0

        # Background sender
        self._shutdown = threading.Event()
        self._sender = threading.Thread(target=self._send_loop, daemon=True, name="Forwarder")
        self._sender.start()

    def submit(self, metrics: List[Metric], events: List[Event], service_checks: List[ServiceCheck]) -> None:
        """Submit data for forwarding"""
        if not metrics and not events and not service_checks:
            return

        with self._cond:
            self._queue.append((metrics, events, service_checks))
            self._cond.notify()

    def _send_loop(self) -> None:
        """Background send loop"""
        while not self._shutdown.is_set():
            with self._cond:
                self._cond.wait(timeout=self._flush_interval)
                if self._queue:
                    to_send = self._queue[:]
                    self._queue = []
                else:
                    to_send = []

            for payload in to_send:
                self._send_payload(payload)

    def _send_payload(self, payload: tuple) -> None:
        """Send a payload to intake"""
        metrics, events, service_checks = payload
        try:
            # Simulate network send
            time.sleep(0.01)

            # Calculate size
            size = len(metrics) * 100 + len(events) * 200 + len(service_checks) * 50

            with self._stats_lock:
                self._payloads_sent += 1
                self._bytes_sent += size
        except Exception:
            with self._stats_lock:
                self._errors += 1

    def stats(self) -> Dict:
        with self._stats_lock:
            return {
                "payloads_sent": self._payloads_sent,
                "bytes_sent": self._bytes_sent,
                "errors": self._errors,
            }

    def count_locks(self) -> int:
        # 1 Condition + 1 stats lock
        return 2

    def shutdown(self) -> None:
        self._shutdown.set()
        with self._cond:
            self._cond.notify()
        self._sender.join(timeout=5.0)


# =============================================================================
# 6. Check Implementations (simulated)
# =============================================================================


def run_cpu_check(instance: CheckInstance) -> tuple:
    """Simulated CPU check"""
    time.sleep(random.uniform(0.01, 0.05))
    metrics = [
        Metric(
            name="system.cpu.user",
            value=random.uniform(0, 100),
            tags=["host:test"],
            timestamp=time.time(),
        ),
        Metric(
            name="system.cpu.system",
            value=random.uniform(0, 50),
            tags=["host:test"],
            timestamp=time.time(),
        ),
    ]
    return (metrics, [], [])


def run_memory_check(instance: CheckInstance) -> tuple:
    """Simulated memory check"""
    time.sleep(random.uniform(0.01, 0.03))
    metrics = [
        Metric(
            name="system.mem.used",
            value=random.uniform(1e9, 8e9),
            tags=["host:test"],
            timestamp=time.time(),
        ),
    ]
    return (metrics, [], [])


def run_http_check(instance: CheckInstance) -> tuple:
    """Simulated HTTP check"""
    time.sleep(random.uniform(0.05, 0.2))

    status = 0 if random.random() > 0.1 else 2
    service_check = ServiceCheck(
        name="http.can_connect",
        status=status,
        tags=["url:" + instance.config.get("url", "http://localhost")],
        timestamp=time.time(),
        message="" if status == 0 else "Connection failed",
    )

    metrics = [
        Metric(
            name="http.response_time",
            value=random.uniform(50, 500),
            tags=service_check.tags,
            timestamp=time.time(),
        ),
    ]
    return (metrics, [], [service_check])


def run_custom_check(instance: CheckInstance) -> tuple:
    """Simulated custom check"""
    time.sleep(random.uniform(0.02, 0.1))
    metrics = [
        Metric(
            name=f"custom.{instance.check_name}.metric",
            value=random.uniform(0, 1000),
            tags=["check:" + instance.check_name],
            timestamp=time.time(),
        ),
    ]

    # Occasionally emit an event
    events = []
    if random.random() < 0.05:
        events.append(
            Event(
                title=f"Custom check event from {instance.check_name}",
                text="Something interesting happened",
                tags=["check:" + instance.check_name],
                timestamp=time.time(),
            )
        )

    return (metrics, events, [])


CHECK_FUNCTIONS = {
    "cpu": run_cpu_check,
    "memory": run_memory_check,
    "http": run_http_check,
    "custom": run_custom_check,
}


# =============================================================================
# 7. Agent Checks Simulator
# =============================================================================


class AgentChecksSimulator:
    """
    Main simulator for agent checks.

    Realistic lock count breakdown:
    - Scheduler: 1 lock (Condition)
    - Collector: 2 locks (Semaphore + results)
    - Aggregator: 4 locks (3 buffers + stats)
    - Forwarder: 2 locks (Condition + stats)

    Total: ~9 base locks
    """

    def __init__(self, num_checks: int = 20, check_concurrency: int = 10):
        self.scheduler = CheckScheduler()
        self.collector = Collector(max_concurrent=check_concurrency)
        self.aggregator = Aggregator(flush_interval=15.0)
        self.forwarder = Forwarder(flush_interval=10.0)

        # Register checks
        check_types = list(CHECK_FUNCTIONS.keys())
        for i in range(num_checks):
            check_type = check_types[i % len(check_types)]
            instance = CheckInstance(
                check_name=check_type,
                instance_id=f"{check_type}_{i}",
                interval=random.uniform(10, 30),  # 10-30 second intervals
                config={"url": f"http://service-{i}.local"} if check_type == "http" else {},
            )
            self.scheduler.register(instance)

        # Workers
        self._shutdown = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._flush_thread: Optional[threading.Thread] = None

        logger.info("AgentChecksSimulator initialized with %s checks", num_checks)

    def start(self) -> None:
        """Start the agent"""
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="Scheduler")
        self._scheduler_thread.start()

        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True, name="Flusher")
        self._flush_thread.start()

    def _scheduler_loop(self) -> None:
        """Main scheduling loop"""
        while not self._shutdown.is_set():
            # Get due checks
            due_checks = self.scheduler.get_due_checks()

            # Run each check
            for instance in due_checks:
                check_func = CHECK_FUNCTIONS.get(instance.check_name, run_custom_check)
                result = self.collector.run_check(instance, check_func)

                if result:
                    metrics, events, service_checks = result
                    if metrics:
                        self.aggregator.add_metrics(metrics)
                    for event in events:
                        self.aggregator.add_event(event)
                    for sc in service_checks:
                        self.aggregator.add_service_check(sc)

            # Wait for next check
            self.scheduler.wait_for_next(timeout=1.0)

    def _flush_loop(self) -> None:
        """Periodic flush loop"""
        while not self._shutdown.is_set():
            time.sleep(15.0)  # Flush every 15 seconds
            metrics, events, service_checks = self.aggregator.flush()
            self.forwarder.submit(metrics, events, service_checks)

    def get_stats(self) -> Dict:
        """Get agent stats"""
        return {
            "collector": self.collector.stats(),
            "aggregator": self.aggregator.stats(),
            "forwarder": self.forwarder.stats(),
        }

    def count_locks(self) -> int:
        """Count total locks"""
        count = 0
        count += self.scheduler.count_locks()
        count += self.collector.count_locks()
        count += self.aggregator.count_locks()
        count += self.forwarder.count_locks()
        return count

    def shutdown(self) -> None:
        self._shutdown.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5.0)
        if self._flush_thread:
            self._flush_thread.join(timeout=5.0)
        self.forwarder.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Agent Checks Lock Usage Simulator")
    parser.add_argument("--checks", type=int, default=20, help="Number of checks")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--concurrency", type=int, default=10, help="Check concurrency")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("AGENT CHECKS SIMULATOR - Realistic Lock Usage Patterns")
    logger.info("=" * 60)

    agent = AgentChecksSimulator(
        num_checks=args.checks,
        check_concurrency=args.concurrency,
    )
    agent.start()

    try:
        # Run for duration
        time.sleep(args.duration)

        stats = agent.get_stats()
        lock_count = agent.count_locks()

        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)
        logger.info("Collector stats: %s", stats["collector"])
        logger.info("Aggregator stats: %s", stats["aggregator"])
        logger.info("Forwarder stats: %s", stats["forwarder"])
        logger.info("Total locks: %s", lock_count)
        logger.info("=" * 60)

    finally:
        agent.shutdown()


if __name__ == "__main__":
    main()
