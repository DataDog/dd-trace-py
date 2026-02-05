# Realistic Lock Scenarios for Profiling

Lock simulators for testing profiler lock detection with realistic workloads.

## Simulators

| Simulator | Models | Locks | Contention |
|-----------|--------|-------|------------|
| `dogweb_simulator.py` | Django web app | 14-25 | Medium |
| `trace_agent_simulator.py` | Trace intake | 30-40 | Medium-High |
| `profiler_backend_simulator.py` | Profile processing | 6-10 | Medium |
| `logs_pipeline_simulator.py` | Log pipeline | 14-20 | High |
| `agent_checks_simulator.py` | Agent checks | 9-12 | Low-Medium |

## Usage

```bash
# Run with dd-trace-py Lock Profiler
DD_PROFILING_ENABLED=true DD_PROFILING_LOCK_ENABLED=true \
ddtrace-run python dogweb_simulator.py --rps 100 --duration 60

# Run standalone
python dogweb_simulator.py --rps 100 --duration 60
python trace_agent_simulator.py --sps 1000 --duration 60
python logs_pipeline_simulator.py --eps 1000 --duration 60
python agent_checks_simulator.py --checks 20 --duration 60

# Benchmark
python benchmark_locks.py --scenario all --duration 30 --inline
```

### With Otel Host Profiler (Linux only, separate terminal)

The Otel Host Profiler uses eBPF and only runs on Linux.

```bash
# Linux (amd64)
./dd-otel-host-profiler-linux-amd64

# Linux (arm64)
./dd-otel-host-profiler-linux-arm64
```

Download from: https://github.com/DataDog/dd-otel-host-profiler/releases

## Lock Patterns

### dd-trace-py Core (~15-20 locks)

| Component | Lock Type | Count |
|-----------|-----------|-------|
| Writers | RLock + Condition | ~4 |
| Encoders | RLock | ~3 |
| Stats/Rate Limiters | Lock | ~3 |
| Tracer/Context | RLock | ~3 |
| Telemetry/Debugging | Lock/RLock | ~4 |

### Common Library Patterns

| Pattern | Lock Type | Count |
|---------|-----------|-------|
| SQLAlchemy Pool | Lock + Condition | ~2/pool |
| Redis Pool | Lock | ~1/client |
| Logging Handlers | RLock | ~2-3 |
| ThreadPoolExecutor | Lock | ~2/executor |

## Design Principles

**Why few locks?** Most Python apps use 20-100 locks total because:
- External stores (Redis, DB) handle atomicity
- Thread-local storage for request context
- Connection pools use shared Conditions

**No per-key/per-session locks**: Would create thousands of locks. Real apps use:
- Atomic external stores (Redis SETNX)
- Single cache-wide locks
- Eventual consistency

### Lock Types

| Type | Use Case |
|------|----------|
| `Lock` | Simple mutual exclusion |
| `RLock` | Reentrant locking |
| `Condition` | Wait for resource (pools) |
| `Semaphore` | Counting/rate limiting |
| `BoundedSemaphore` | Concurrency limiting |
