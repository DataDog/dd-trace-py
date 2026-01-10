#!/usr/bin/env python3
"""
dogweb Simulator - Simulates Datadog's Django-based web application

This simulates the lock usage patterns of a large Django application like dogweb:
- Database connection pooling (SQLAlchemy-style with Condition variables)
- Cache layer (Redis/memcached client with connection pools)
- Logging with thread-safe handlers
- Background task coordination (Celery-style)
- Framework singletons and lazy initialization
- Request metrics and rate limiting

Realistic lock counts: 50-150 locks per process
- This is based on analysis of actual Python web apps:
  - dd-trace-py core: ~30-40 locks
  - SQLAlchemy connection pools: ~3 locks per pool
  - Redis clients: ~2 locks per client
  - Logging handlers: ~2-3 locks
  - Background workers: ~5-10 locks

Contention level: Medium (most locks are low-contention)
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
import queue
import random
import threading
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


# Configure logging with a thread-safe handler
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# 1. Database Connection Pool (SQLAlchemy QueuePool style)
#
# Real SQLAlchemy pools use:
# - 1 RLock for pool state
# - 1 Condition for waiting on connections
# - NO per-connection locks (connections are checked out atomically)
# =============================================================================


class ConnectionPool:
    """
    Simulates a database connection pool like SQLAlchemy's QueuePool.

    Lock pattern: 1 Condition (with internal lock) for the entire pool.
    This is realistic - SQLAlchemy uses a similar pattern.

    In real dogweb: 2-3 pools (main, replica, analytics), ~20 connections each
    """

    def __init__(self, name: str, pool_size: int = 20, max_overflow: int = 10, timeout: float = 30.0):
        self.name = name
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._timeout = timeout

        # Single condition variable for the pool (like SQLAlchemy)
        # The Condition internally uses a Lock
        self._cond = threading.Condition(threading.Lock())

        # Connection tracking
        self._pool: queue.Queue = queue.Queue(maxsize=pool_size)
        self._overflow_count = 0
        self._checked_out = 0

        # Pre-populate pool with "connections"
        for i in range(pool_size):
            self._pool.put(self._create_connection(i))

        logger.debug("ConnectionPool '%s' initialized with %s connections", name, pool_size)

    def _create_connection(self, conn_id: int) -> Dict[str, Any]:
        """Simulate connection creation (expensive operation)"""
        time.sleep(0.001)  # Simulate connection setup
        return {"id": conn_id, "pool": self.name, "created_at": time.time()}

    def checkout(self) -> Optional[Dict[str, Any]]:
        """Get a connection from the pool"""
        with self._cond:
            # Try to get from pool first
            try:
                conn = self._pool.get_nowait()
                self._checked_out += 1
                return conn
            except queue.Empty:
                pass

            # Try overflow
            if self._overflow_count < self._max_overflow:
                self._overflow_count += 1
                self._checked_out += 1
                return self._create_connection(1000 + self._overflow_count)

            # Wait for a connection
            deadline = time.time() + self._timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"Connection pool '{self.name}' exhausted")

                self._cond.wait(timeout=remaining)

                try:
                    conn = self._pool.get_nowait()
                    self._checked_out += 1
                    return conn
                except queue.Empty:
                    continue

    def checkin(self, conn: Dict[str, Any]) -> None:
        """Return a connection to the pool"""
        with self._cond:
            self._checked_out -= 1

            if conn["id"] >= 1000:  # Overflow connection
                self._overflow_count -= 1
            else:
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    pass

            self._cond.notify()

    def count_locks(self) -> int:
        """Count locks in this pool"""
        # 1 Condition (which wraps 1 Lock internally)
        return 1


# =============================================================================
# 2. Cache Client (Redis-style with connection pool)
#
# Real Redis clients use:
# - 1 Lock for connection pool
# - NO per-key locks (Redis itself handles atomicity)
# =============================================================================


class CacheClient:
    """
    Simulates a cache client like redis-py.

    Lock pattern: 1 Lock for connection pool, NO per-key locks.
    Real Redis clients don't use per-key locks because Redis commands are atomic.
    """

    def __init__(self, name: str, pool_size: int = 10):
        self.name = name

        # Connection pool with single lock (like redis-py's ConnectionPool)
        self._pool: List[Any] = [f"conn_{i}" for i in range(pool_size)]
        self._pool_lock = threading.Lock()
        self._in_use: set = set()

        # Stats (single lock for all stats)
        self._stats_lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._commands = 0

    def _get_connection(self) -> str:
        """Get a connection from the pool"""
        with self._pool_lock:
            for conn in self._pool:
                if conn not in self._in_use:
                    self._in_use.add(conn)
                    return conn
            # All connections in use, create overflow
            conn = f"overflow_{len(self._in_use)}"
            self._in_use.add(conn)
            return conn

    def _return_connection(self, conn: str) -> None:
        """Return a connection to the pool"""
        with self._pool_lock:
            self._in_use.discard(conn)

    def get(self, key: str) -> Optional[str]:
        """Get value from cache"""
        conn = self._get_connection()
        try:
            # Simulate network latency
            time.sleep(random.uniform(0.0001, 0.0005))

            with self._stats_lock:
                self._commands += 1
                # Simulate 90% hit rate
                if random.random() < 0.9:
                    self._hits += 1
                    return f"value_{key}"
                else:
                    self._misses += 1
                    return None
        finally:
            self._return_connection(conn)

    def set(self, key: str, value: str, ttl: int = 300) -> bool:
        """Set value in cache"""
        conn = self._get_connection()
        try:
            # Simulate network latency
            time.sleep(random.uniform(0.0001, 0.0003))

            with self._stats_lock:
                self._commands += 1
            return True
        finally:
            self._return_connection(conn)

    def count_locks(self) -> int:
        """Count locks in this client"""
        # 1 pool lock + 1 stats lock
        return 2


# =============================================================================
# 3. Logging Handler
#
# Real Python logging uses locks in handlers.
# =============================================================================


class MetricsLogger:
    """
    Simulates thread-safe logging/metrics collection.

    Lock pattern: 1 RLock per handler (Python's logging.Handler uses RLock)
    """

    def __init__(self):
        # RLock like Python's logging.Handler
        self._lock = threading.RLock()
        self._buffer: List[str] = []
        self._flush_count = 0

    def log(self, message: str) -> None:
        """Log a message"""
        with self._lock:
            self._buffer.append(message)
            if len(self._buffer) >= 100:
                self._flush()

    def _flush(self) -> None:
        """Flush buffer (must be called with lock held)"""
        self._buffer.clear()
        self._flush_count += 1

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 4. Background Task Queue (Celery-style)
#
# Real Celery uses locks for:
# - Result backend connection
# - Task state updates
# - Rate limiting (usually via Redis, not in-memory)
# =============================================================================


class TaskQueue:
    """
    Simulates a background task queue like Celery.

    Lock pattern:
    - 1 Lock for task queue
    - 1 Lock for results store
    - 1 Lock for worker coordination
    """

    def __init__(self, num_workers: int = 4):
        self._queue: queue.Queue = queue.Queue(maxsize=1000)

        # Result storage with lock
        self._results: Dict[str, Any] = {}
        self._results_lock = threading.Lock()

        # Worker state
        self._active_tasks = 0
        self._active_lock = threading.Lock()

        # Shutdown coordination
        self._shutdown = threading.Event()

        # Workers
        self._workers: List[threading.Thread] = []
        for i in range(num_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"TaskWorker-{i}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def _worker_loop(self):
        """Worker thread main loop"""
        while not self._shutdown.is_set():
            try:
                task = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self._active_lock:
                self._active_tasks += 1

            try:
                task_id, func, args, kwargs = task
                result = func(*args, **kwargs)

                with self._results_lock:
                    self._results[task_id] = {"status": "success", "result": result}
            except Exception as e:
                with self._results_lock:
                    self._results[task_id] = {"status": "error", "error": str(e)}
            finally:
                with self._active_lock:
                    self._active_tasks -= 1
                self._queue.task_done()

    def submit(self, func, *args, **kwargs) -> str:
        """Submit a task to the queue"""
        task_id = f"task_{time.time_ns()}_{random.randint(1000, 9999)}"
        self._queue.put((task_id, func, args, kwargs), timeout=5.0)
        return task_id

    def get_result(self, task_id: str, timeout: float = 30.0) -> Optional[Dict]:
        """Get task result"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._results_lock:
                if task_id in self._results:
                    return self._results.pop(task_id)
            time.sleep(0.01)
        return None

    def count_locks(self) -> int:
        """Count locks in task queue"""
        # 1 results lock + 1 active tasks lock + 1 Event (internal lock)
        return 3

    def shutdown(self):
        """Shutdown the task queue"""
        self._shutdown.set()
        for worker in self._workers:
            worker.join(timeout=5.0)


# =============================================================================
# 5. Framework Singletons (lazy initialization)
#
# Common pattern in Django/Flask for lazy-loaded components
# =============================================================================


class LazySingleton:
    """
    Simulates lazy initialization pattern common in frameworks.
    Uses double-checked locking pattern.
    """

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = {"initialized_at": time.time()}
        return cls._instance


# =============================================================================
# 6. Rate Limiter
#
# Simple token bucket rate limiter
# =============================================================================


class RateLimiter:
    """
    Token bucket rate limiter.

    Lock pattern: 1 Lock for atomic token operations
    """

    def __init__(self, rate: float = 100.0, burst: int = 100):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_update = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens"""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_update = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def count_locks(self) -> int:
        return 1


# =============================================================================
# 7. Request Handler (Django View simulation)
# =============================================================================


@dataclass
class Request:
    """Simulated HTTP request"""

    request_id: str
    user_id: str
    path: str
    method: str = "GET"


class DogwebSimulator:
    """
    Main simulator that orchestrates all components like a real Django app.

    Realistic lock count breakdown:
    - DB pools: ~1 condition per pool (3 pools = 3 locks)
    - Cache clients: ~2 locks per client (2 clients = 4 locks)
    - Logging: ~1-2 locks
    - Task queue: ~3 locks
    - Rate limiter: ~1 lock
    - Framework singletons: ~1 lock
    - Metrics: ~1 lock

    Total: ~15-20 base locks (not including library internals)
    """

    def __init__(self, num_db_pools: int = 3, pool_size: int = 20, num_cache_clients: int = 2, num_workers: int = 4):
        # Multiple database pools (main, replica, analytics)
        self.db_pools = [
            ConnectionPool(name=f"pool_{i}", pool_size=pool_size, max_overflow=10) for i in range(num_db_pools)
        ]

        # Cache clients (Redis, Memcached)
        self.cache_clients = [CacheClient(name=f"cache_{i}", pool_size=10) for i in range(num_cache_clients)]

        # Logging handler
        self.metrics_logger = MetricsLogger()

        # Background task queue
        self.task_queue = TaskQueue(num_workers=num_workers)

        # Rate limiter
        self.rate_limiter = RateLimiter(rate=1000, burst=100)

        # Request metrics (single lock)
        self._metrics_lock = threading.Lock()
        self._request_count = 0
        self._total_latency = 0.0
        self._errors = 0

        logger.info("DogwebSimulator initialized with %s DB pools, %s cache clients", num_db_pools, num_cache_clients)

    def handle_request(self, request: Request) -> Dict:
        """
        Simulate handling a web request (Django view).
        This exercises realistic lock patterns.
        """
        start_time = time.time()

        # 1. Rate limiting check
        if not self.rate_limiter.acquire():
            with self._metrics_lock:
                self._errors += 1
            return {"status": "rate_limited"}

        # 2. Check cache for common data
        cache = random.choice(self.cache_clients)
        cache.get(f"user:{request.user_id}:prefs")  # Check for cached data

        # 3. Query database
        db_pool = random.choice(self.db_pools)
        conn = db_pool.checkout()
        try:
            # Simulate DB query
            time.sleep(random.uniform(0.001, 0.005))
            result = {"data": f"result_for_{request.path}"}
        finally:
            db_pool.checkin(conn)

        # 4. Maybe cache the result
        if random.random() < 0.3:
            cache.set(f"request:{request.request_id}", str(result))

        # 5. Log metrics
        self.metrics_logger.log(f"Request {request.request_id} completed")

        # 6. Maybe enqueue background task (10% of requests)
        if random.random() < 0.1:

            def background_work():
                time.sleep(0.01)
                return "completed"

            self.task_queue.submit(background_work)

        # 7. Update request metrics
        latency = time.time() - start_time
        with self._metrics_lock:
            self._request_count += 1
            self._total_latency += latency

        return {"status": "ok", "latency_ms": latency * 1000, **result}

    def get_metrics(self) -> Dict:
        """Get current metrics"""
        with self._metrics_lock:
            avg_latency = self._total_latency / max(1, self._request_count)
            return {"request_count": self._request_count, "avg_latency_ms": avg_latency * 1000, "errors": self._errors}

    def count_locks(self) -> int:
        """Count total locks in the system"""
        count = 0

        # DB pools (1 condition each)
        for pool in self.db_pools:
            count += pool.count_locks()

        # Cache clients
        for cache in self.cache_clients:
            count += cache.count_locks()

        # Logging
        count += self.metrics_logger.count_locks()

        # Task queue
        count += self.task_queue.count_locks()

        # Rate limiter
        count += self.rate_limiter.count_locks()

        # Metrics lock
        count += 1

        # Framework singleton
        count += 1  # LazySingleton._lock

        return count

    def shutdown(self):
        """Clean shutdown"""
        self.task_queue.shutdown()


# =============================================================================
# Main Entry Point
# =============================================================================


def simulate_traffic(simulator: DogwebSimulator, rps: int, duration_seconds: int):
    """Simulate realistic traffic to the service"""

    logger.info("Starting traffic simulation: %s RPS for %ss", rps, duration_seconds)

    def make_request():
        request = Request(
            request_id=f"req_{time.time_ns()}",
            user_id=f"user_{random.randint(1, 1000)}",
            path=random.choice(
                [
                    "/api/v1/dashboards",
                    "/api/v1/monitors",
                    "/api/v1/metrics",
                    "/api/v1/hosts",
                    "/api/v1/events",
                ]
            ),
        )
        try:
            simulator.handle_request(request)
        except Exception as e:
            logger.error("Request failed: %s", e)

    # Use thread pool to generate traffic
    with ThreadPoolExecutor(max_workers=min(rps, 100)) as executor:
        start_time = time.time()
        request_interval = 1.0 / rps

        while time.time() - start_time < duration_seconds:
            executor.submit(make_request)
            time.sleep(request_interval)

    logger.info("Traffic simulation complete")


def main():
    parser = argparse.ArgumentParser(description="Dogweb Lock Usage Simulator")
    parser.add_argument("--rps", type=int, default=100, help="Requests per second")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--db-pools", type=int, default=3, help="Number of DB pools")
    parser.add_argument("--pool-size", type=int, default=20, help="Connections per pool")
    parser.add_argument("--cache-clients", type=int, default=2, help="Number of cache clients")
    parser.add_argument("--workers", type=int, default=4, help="Background task workers")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("DOGWEB SIMULATOR - Realistic Lock Usage Patterns")
    logger.info("=" * 60)

    simulator = DogwebSimulator(
        num_db_pools=args.db_pools,
        pool_size=args.pool_size,
        num_cache_clients=args.cache_clients,
        num_workers=args.workers,
    )

    try:
        # Trigger lazy singleton initialization
        LazySingleton.get_instance()

        # Run traffic simulation
        simulate_traffic(simulator, args.rps, args.duration)

        # Print final stats
        metrics = simulator.get_metrics()
        lock_count = simulator.count_locks()

        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info("=" * 60)
        logger.info("Total requests: %s", metrics["request_count"])
        logger.info("Avg latency: %.2f ms", metrics["avg_latency_ms"])
        logger.info("Errors: %s", metrics["errors"])
        logger.info("Total locks created: %s", lock_count)
        logger.info("=" * 60)

    finally:
        simulator.shutdown()


if __name__ == "__main__":
    main()
