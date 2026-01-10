# Realistic Lock Scenarios for Profiling

These scripts simulate lock usage patterns based on Datadog's internal Python services.

## Summary of Work Done

This work analyzed lock usage patterns in dd-trace-py and typical Python/Django applications
to create realistic lock simulators for profiler testing.

### Key Findings

1. **Previous lock estimates were unrealistic**: Original estimates of 1,000-5,000 locks per
   service were based on assumptions about per-key/per-session locking that don't reflect
   real Python application patterns.

2. **Actual lock counts are much lower**: Analysis of dd-trace-py core found ~15-20 locks.
   Typical Django apps with SQLAlchemy/Redis use 20-100 locks total.

3. **Common anti-patterns avoided**:
   - No per-key cache locks (Redis handles atomicity)
   - No per-session locks (Django uses external storage)
   - No per-connection locks (pools use shared Conditions)

### Analysis Methodology

Lock usage was analyzed by:
- Searching dd-trace-py for `Lock()`, `RLock()`, `Semaphore()`, `BoundedSemaphore()`, `Condition()`
- Examining SQLAlchemy, Redis, and Django connection pooling patterns
- Reviewing dd-trace-py's internal writer, encoder, and stats processor implementations

### Simulators Created

| Simulator | Models | Lock Count | Primary Lock Patterns |
|-----------|--------|------------|----------------------|
| `dogweb_simulator.py` | Django web app (dogweb) | 14-25 | Connection pools, cache, task queue |
| `trace_agent_simulator.py` | Trace intake service | 30-40 | Span buffers, writers, sampling |
| `profiler_backend_simulator.py` | Profile processing | 6-10 | Symbol cache, aggregation |
| `logs_pipeline_simulator.py` | Log processing pipeline | 14-20 | Intake buffers, parsing, routing |
| `agent_checks_simulator.py` | Datadog Agent checks | 9-12 | Scheduler, collector, aggregator |

### Next Steps (for DoE repo)

These simulators can be translated to the DoE repo for:
1. Profiler lock detection testing with realistic workloads
2. Benchmarking lock profiler overhead
3. Validating lock contention detection accuracy

---

## Lock Count Analysis

Based on analysis of dd-trace-py and typical Python web applications:

### Actual Lock Counts in dd-trace-py Core

| Component | Lock Type | Count |
|-----------|-----------|-------|
| Writers (AgentWriter, etc.) | RLock + Condition | ~4 |
| Encoders | RLock | ~3 |
| Stats Processor | Lock | ~1 |
| Rate Limiters | Lock | ~2 |
| Tracer | RLock | ~1 |
| Context Management | RLock | ~2 |
| Telemetry | Lock | ~2 |
| Debugging/Probes | RLock | ~2 |
| **Total dd-trace-py Core** | | **~15-20** |

### Common Python Library Lock Patterns

| Library/Pattern | Lock Type | Typical Count |
|-----------------|-----------|---------------|
| SQLAlchemy Pool | Lock + Condition | ~2 per pool |
| Redis Connection Pool | Lock | ~1 per client |
| Logging Handlers | RLock | ~2-3 |
| ThreadPoolExecutor | Lock | ~2 per executor |
| Lazy Singletons | Lock | ~3-5 |

**Key Insight**: Most Python applications use **20-100 locks total**, not thousands.
Per-key or per-session locks are rare because:
- External stores (Redis, databases) handle atomicity
- Thread-local storage is preferred for request context
- Connection pools use shared conditions, not per-connection locks

## Services Simulated

### 1. dogweb_simulator.py - Django-based web application (like dogweb)

Simulates a large Django application with:
- Database connection pooling (SQLAlchemy-style with Condition)
- Cache layer (Redis client with connection pool)
- Background task coordination (Celery-style)
- Rate limiting
- Logging handlers

**Lock breakdown:**
- DB pools: 1 Condition per pool (3 pools = 3)
- Cache clients: 2 locks per client (2 clients = 4)
- Task queue: 3 locks
- Rate limiter: 1 lock
- Logging: 1 lock
- Metrics: 1 lock
- Framework singletons: 1 lock

**Estimated locks: 15-25**

### 2. trace_agent_simulator.py - Trace intake/processing service

Simulates dd-trace-py's trace processing with:
- Span buffer management with Conditions
- Concurrent trace writers
- Sampling decision coordination
- Periodic flush operations

**Lock breakdown:**
- Span buffers: 2 locks per buffer (10 buffers = 20)
- Sampler: 1 lock
- Stats aggregator: 1 lock
- Writers: 4 locks per writer (2 writers = 8)
- Buffers management: 1 lock
- Metrics: 1 lock

**Estimated locks: 30-40**

### 3. profiler_backend_simulator.py - Profile processing service

Simulates profile data processing with:
- Profile data ingestion
- Symbol resolution with caching
- Concurrent profile aggregation
- Rate limiting

**Lock breakdown:**
- Symbol cache: 1 RLock
- Aggregator: 1 Lock
- Ingestion queue: 3 locks (Condition, BoundedSemaphore, stats)
- Metrics: 1 lock

**Estimated locks: 6-10**

### 4. logs_pipeline_simulator.py - Log processing pipeline

Simulates a high-throughput log processing service with:
- Intake buffers with backpressure
- Parser pool with concurrency limiting
- Index routing for log destinations
- Archive writer for cold storage
- Rate limiting per source

**Lock breakdown:**
- Intake buffers: 2 locks per buffer (3 buffers = 6)
- Parser pool: 2 locks (Semaphore + stats)
- Router: 2 locks (rules + stats)
- Archive writer: 2 locks (Condition + stats)
- Rate limiter: 1 lock
- Metrics: 1 lock

**Estimated locks: 14-20**

### 5. agent_checks_simulator.py - Datadog Agent checks system

Simulates the Python parts of the Datadog Agent with:
- Check scheduler with periodic execution
- Collector running checks in parallel
- Aggregator for metrics/events/service checks
- Forwarder for sending data to intake

**Lock breakdown:**
- Scheduler: 1 lock (Condition)
- Collector: 2 locks (Semaphore + results)
- Aggregator: 4 locks (3 data buffers + stats)
- Forwarder: 2 locks (Condition + stats)

**Estimated locks: 9-12**

## Usage

```bash
# Run with Lock Profiler enabled
DD_PROFILING_ENABLED=true \
DD_PROFILING_LOCK_ENABLED=true \
DD_SERVICE=dogweb-simulator \
ddtrace-run python dogweb_simulator.py

# Run with Otel host profiler (in separate terminal)
./dd-otel-host-profiler-linux-amd64
```

## Expected Lock Counts (Realistic Estimates)

| Service | Estimated Locks | Lock Types | Contention Level |
|---------|-----------------|------------|------------------|
| dogweb | 15-25 | Lock, RLock, Condition | Medium |
| trace-agent | 30-40 | Lock, Condition | Medium-High |
| profiler-backend | 6-10 | Lock, RLock, BoundedSemaphore | Medium |
| logs-pipeline | 14-20 | Lock, Condition, Semaphore | High |
| agent-checks | 9-12 | Lock, Condition, Semaphore | Low-Medium |

**Note**: Previous estimates of 1,000-5,000 locks were unrealistic. Real Python applications
use far fewer locks because:
1. External storage (Redis, databases) handles concurrency
2. Thread-local storage is used for request context
3. Connection pools share conditions rather than per-connection locks
4. Most caches use a single lock, not per-key locks

## Benchmarking

```bash
# Run benchmark to measure actual lock counts
python benchmark_locks.py --scenario all --duration 30 --inline

# Compare with and without lock profiling
python benchmark_locks.py --scenario dogweb --compare-profiling
```

## Implementation Notes

### Why No Per-Key Locks?

Per-key locking (e.g., one lock per cache key) creates lock explosion:
- 10,000 cache keys = 10,000 locks
- This is unrealistic for production Python apps

Real applications avoid this by:
1. Using atomic external stores (Redis SETNX, database transactions)
2. Using single cache-wide locks (with short hold times)
3. Accepting eventual consistency for read-heavy workloads

### Why Condition Variables?

Connection pools use `threading.Condition` because:
1. It provides wait/notify semantics for "wait for available connection"
2. One Condition handles all waiters efficiently
3. It internally wraps a single Lock

### Lock Types Summary

| Type | Use Case | Example |
|------|----------|---------|
| `Lock` | Simple mutual exclusion | Stats counters |
| `RLock` | Reentrant locking | Writer buffers |
| `Condition` | Wait for resource | Connection pools |
| `Semaphore` | Counting resources | Rate limiters |
| `BoundedSemaphore` | Limited concurrency | Processing slots |
| `Event` | One-time signals | Shutdown coordination |
