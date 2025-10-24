# RLock Profiling Demo Scripts

This directory contains stress tests and demos for RLock (Reentrant Lock) profiling in production.

## Quick Start

```bash
# Interactive menu (recommended)
./scripts/run_rlock_demos.sh

# Or run scripts directly:

# Quick verification (30 seconds)
python scripts/rlock_simple_demo.py

# Heavy contention for obvious samples (60 seconds)
python scripts/rlock_heavy_contention.py

# Comprehensive stress test (configurable)
python scripts/rlock_stress_test.py 120 20  # 120s, 20 workers
```

**View results:** https://app.datadoghq.com/profiling

## Background

The Lock profiler now supports `threading.RLock` in addition to `threading.Lock`. These scripts demonstrate RLock profiling using the Datadog profiler API directly (not `ddtrace-run`, which has known issues with the lock profiler).

## Scripts

### 1. `rlock_simple_demo.py` - Quick Verification

A minimal demo to quickly verify RLock profiling is working.

**Usage:**
```bash
python scripts/rlock_simple_demo.py
```

**What it does:**
- Runs for ~30 seconds
- Creates 10 threads with RLock contention
- Demonstrates both simple and reentrant lock patterns
- Shows clear acquire/release/wait samples

**Best for:** Quick smoke test, development verification

---

### 2. `rlock_stress_test.py` - Comprehensive Load Test

A comprehensive stress test with multiple workload patterns.

**Usage:**
```bash
# Default: 60 seconds, 10 workers
python scripts/rlock_stress_test.py

# Custom duration and worker count
python scripts/rlock_stress_test.py 120 20  # 120 seconds, 20 workers
```

**What it does:**
- **Phase 1:** High contention workload (shows wait times)
- **Phase 2:** Reentrant lock patterns (demonstrates RLock-specific behavior)
- **Phase 3:** Mixed realistic workload (cache access, counters, etc.)

**Best for:** Production readiness testing, load testing, demo presentations

---

### 3. `rlock_heavy_contention.py` - Maximum Visibility Demo

Creates EXTREME lock contention for dramatic, obvious profiling samples.

**Usage:**
```bash
python scripts/rlock_heavy_contention.py
```

**What it does:**
- 3 writer threads (hold lock for 10ms - intentionally long)
- 8 aggressive reader threads (constant acquisition attempts)
- 4 nested reentrant threads (deep RLock reentrancy)
- Runs for 60 seconds with live progress
- Produces very high wait time samples

**Best for:** Demos, presentations, proving RLock profiling works, visual impact

---

## Comparison Table

| Feature | Simple Demo | Stress Test | Heavy Contention |
|---------|-------------|-------------|------------------|
| Duration | ~30s | 60-300s (configurable) | 60s |
| Threads | 10 | 10-50 (configurable) | 15 |
| Contention Level | Moderate | Varies by phase | Extreme |
| Complexity | Minimal | Comprehensive | Focused |
| Reentrancy Examples | Yes | Yes | Deep (3 levels) |
| Best For | Quick check | Production testing | Demos/Presentations |
| Sample Visibility | Good | Good | Excellent |

**Recommendation:**
- **First time?** Start with `rlock_simple_demo.py`
- **Testing for prod?** Use `rlock_stress_test.py`
- **Need to impress?** Run `rlock_heavy_contention.py`

---

## Expected Profiling Data

When running these scripts, you should see the following in the Datadog profiling UI:

### Lock Acquire Samples
- Show where locks are acquired
- Include stack traces showing calling code
- Label: `lock-acquire`

### Lock Release Samples
- Show where locks are released
- Include hold duration
- Label: `lock-release`

### Lock Wait Samples
- Show contention (time spent waiting to acquire)
- Identify bottlenecks
- Label: `lock-wait`

### RLock-Specific Patterns
- Multiple acquisitions by the same thread
- Nested `with` statements with the same lock
- Different from regular `Lock` behavior

---

## Viewing Results

1. **Run a script:**
   ```bash
   python scripts/rlock_simple_demo.py
   ```

2. **View in Datadog:**
   - Navigate to: https://app.datadoghq.com/profiling
   - Filter by service: `rlock-simple-demo` or `rlock-stress-test`
   - Look for lock-related samples in the flamegraph
   - Check the "Lock" profile type tab

3. **Key Metrics to Check:**
   - Lock acquire time distribution
   - Lock hold time (release samples)
   - Lock wait time (contention)
   - Thread distribution
   - Reentrancy patterns (RLock-specific)

---

## Configuration

### Using Local Agent (Default)

All scripts are configured to send profiling data to your **local Datadog agent**:

```python
os.environ["DD_PROFILING_ENABLED"] = "1"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "1"
os.environ["DD_SERVICE"] = "rlock-stress-test"
os.environ["DD_ENV"] = "dev"
os.environ["DD_AGENT_HOST"] = "localhost"  # Uses local agent
```

**Requirements:**
- Datadog agent must be running
- Agent configured with API key in `datadog.yaml`
- **No need to set `DD_API_KEY` environment variable**

You can customize service/env:
```bash
DD_ENV=production DD_SERVICE=my-service python scripts/rlock_stress_test.py
```

### Alternative: Agentless Mode

To bypass the agent and send directly to Datadog:

```bash
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"
unset DD_AGENT_HOST
python scripts/rlock_stress_test.py
```

---

## Troubleshooting

### No lock samples appearing?

1. **Check agent is running:**
   ```bash
   datadog-agent status
   ```

2. **Verify agent receives profiling data:**
   ```bash
   tail -f /var/log/datadog/agent.log | grep profil
   ```

3. **Check profiler is enabled:**
   ```bash
   DD_TRACE_DEBUG=true python scripts/rlock_simple_demo.py 2>&1 | grep -i lock
   ```

4. **Verify connection to agent:**
   ```bash
   netstat -an | grep 8126  # Agent APM port
   ```

### Samples look incorrect?

1. **Verify RLock support:**
   - Check ddtrace version supports RLock
   - Review recent changes to `ddtrace/profiling/collector/_lock.py`

2. **Check sampling rate:**
   - Default capture rate may be too low for short tests
   - Consider longer test duration

---

## Development Notes

### Why not use `ddtrace-run`?

The lock profiler has known issues with `ddtrace-run`. Using the API directly (via `import ddtrace.profiling.auto`) is more reliable.

### Test Duration

- **Simple demo:** ~30 seconds (quick verification)
- **Stress test:** 60-300 seconds recommended (production-like load)
- Longer tests generate more statistically significant data

### Worker Count

- Start with 10 workers for basic testing
- Increase to 20-50 for realistic production simulation
- Higher worker counts = more contention = better wait time samples

---

## Example Output

```
================================================================================
Simple RLock Profiling Demo
================================================================================
Service: rlock-simple-demo
Environment: dev

Running 30 second test...
Watch for lock samples in Datadog APM UI
================================================================================

Worker 0 completed
Worker 1 completed
Worker 2 completed
...

✅ All threads completed
Final counter value: 1000
Expected value: ~1000

Waiting 10 seconds for profile upload...

================================================================================
Demo completed!
Check Datadog profiling UI for lock samples at:
https://app.datadoghq.com/profiling
================================================================================
```

---

## Contributing

When modifying these scripts:
- Keep them simple and focused
- Add clear comments explaining lock patterns
- Test with various worker counts and durations
- Verify samples appear correctly in Datadog UI

---

## Related Files

- `ddtrace/profiling/collector/_lock.py` - Lock profiler implementation
- `ddtrace/profiling/collector/threading.py` - Threading-specific collectors
- `tests/profiling_v2/collector/test_threading.py` - Unit tests

