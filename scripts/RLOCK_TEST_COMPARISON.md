# RLock Testing: Unit Tests vs Production Stress Test

## Quick Comparison

| Aspect | Unit Tests | Production Stress Test |
|--------|------------|----------------------|
| **Location** | `tests/profiling_v2/collector/test_threading.py` | `scripts/rlock_production_stress_test.py` |
| **Profiler Setup** | `ddup.config(output_filename=...)` | `ddtrace.profiling.auto` |
| **Upload Target** | Local pprof files (`/tmp/test_name.pid`) | Datadog agent or API |
| **Validation** | Automatic assertions | Manual UI inspection |
| **Lock Patterns** | Named, class member, global, private, anonymous | Same patterns, more intense |
| **Duration** | ~1 second per test | Configurable (default 120s) |
| **Workers** | 1-2 threads typically | 8+ concurrent workers |
| **Purpose** | Validate collector logic | Validate production pipeline |

## Key Difference: The Gap

**Unit tests validate:**
```
Lock Creation → Collector → ddup → pprof file → parse → assert
```
✅ Tests pass → Collector correctly captures lock names

**Production stress test validates:**
```
Lock Creation → Collector → ddup → Agent/API → Backend → UI
```
❓ Are lock names lost somewhere in this pipeline?

## Running Both

### 1. Run Unit Tests (Known to Pass)

```bash
# Run RLock-specific unit tests
cd /Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-2
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector -v

# Specific tests for lock names
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector::test_class_member_lock -v
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector::test_global_locks -v
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector::test_private_lock -v
```

**Expected:** All tests pass ✅

### 2. Run Production Stress Test

```bash
# Set up agent or agentless mode
export DD_AGENT_HOST="localhost"  # or DD_API_KEY="..." for agentless

# Run stress test
./scripts/run_rlock_production_stress_test.sh 120 8
```

**Expected:** Lock names visible in Datadog UI ✅
**Actual (suspected):** Only threading.py:XXX names visible? ❌

## Code Pattern Comparison

### Unit Test Pattern

```python
# tests/profiling_v2/collector/test_threading.py
def test_lock_events(self):
    with self.collector_class(capture_pct=100):
        lock = self.lock_class()  # !CREATE! test_lock_events
        lock.acquire()  # !ACQUIRE! test_lock_events
        lock.release()  # !RELEASE! test_lock_events
    
    ddup.upload()
    
    profile = pprof_utils.parse_newest_profile(self.output_filename)
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                lock_name="lock",  # ← Validates variable name!
                # ... other fields
            ),
        ],
    )
```

### Production Stress Test Pattern

```python
# scripts/rlock_production_stress_test.py
def test_local_named_locks(worker_id: int, iterations: int):
    for i in range(iterations):
        request_lock = threading.RLock()
        response_lock = threading.RLock()
        
        with request_lock:  # Should capture "request_lock" name
            time.sleep(0.0005)
        
        with response_lock:  # Should capture "response_lock" name
            time.sleep(0.0005)

# Profiler automatically started via ddtrace.profiling.auto
# Samples automatically uploaded to agent/API
# Validation: manually check Datadog UI for lock names
```

## What Lock Names Look Like

### In Unit Test Assertions

```python
lock_name="lock"
lock_name="user_session_lock"
lock_name="_Foo__lock"  # Private member (mangled)
```

### In pprof Files (from unit tests)

```
Sample:
  lock-name: test_threading.py:405:lock
  filename: test_threading.py
  lineno: 405
  function: test_lock_events
```

### In Datadog UI (expected)

Lock name should appear as:
```
test_threading.py:405:lock
```

Or for class members:
```
rlock_production_stress_test.py:125:user_session_lock
```

### In Datadog UI (if broken)

Lock name might appear as:
```
threading.py:123
<threading.RLock object at 0x7f8b2c3d4e50>
```

## Debugging Steps

### Step 1: Verify Unit Tests Work

```bash
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector::test_lock_events -v -s
```

If this fails, the collector itself is broken.
If this passes, continue to Step 2.

### Step 2: Inspect Raw pprof from Stress Test

Modify `rlock_production_stress_test.py` to also write local pprof files:

```python
# Add at the top after environment setup
os.environ["DD_PROFILING_OUTPUT_PPROF"] = "/tmp/rlock_stress_test"

# At the end, parse the file
from tests.profiling.collector import pprof_utils
profile = pprof_utils.parse_newest_profile("/tmp/rlock_stress_test")
# Check if lock names are in the pprof file
```

If lock names are in the pprof file but not in UI, issue is in upload/backend.
If lock names are NOT in the pprof file, issue is in production collector startup.

### Step 3: Compare Collector Configuration

**Unit tests:**
```python
ThreadingRLockCollector(capture_pct=100)
```

**Production:**
```python
# Via ddtrace.profiling.auto, which uses default configuration
# Check: ddtrace.settings.profiling.config.lock.*
```

Differences?
- `capture_pct` (unit tests use 100, production uses default)
- `name_inspect_dir` (enables class member detection)
- `nframes` (stack depth)
- `endpoint_collection_enabled`

### Step 4: Check Environment Differences

Unit tests run in controlled environment:
- `ddup.config()` called explicitly
- Single process, single test
- Local filesystem output

Production:
- Auto-started via import side effect
- May have different threading model
- Network upload with retry/buffering

## Common Issues

### Issue 1: Collector Not Started in Production

**Symptom:** No lock samples at all

**Check:**
```python
from ddtrace.profiling.collector.threading import ThreadingRLockCollector
# Is it registered and running?
```

### Issue 2: Frame Inspection Broken

**Symptom:** Samples exist but all show `threading.py` locations

**Cause:** Lock name detection uses `sys._getframe()` to walk stack
- If stack depth calculation is wrong, might look at wrong frame
- If variables are optimized away, might not find name

**Check:** Enable debug logging

### Issue 3: Name Lost in Serialization

**Symptom:** Lock names in collector but not in uploaded data

**Cause:** Issue in ddup serialization or agent processing

**Check:** Compare local pprof output with uploaded data

### Issue 4: UI Display Issue

**Symptom:** Lock names in backend but not displayed in UI

**Cause:** Frontend bug, not collector issue

**Check:** Raw API response vs UI display

## Investigation Checklist

- [ ] Unit tests pass (confirms collector works in isolation)
- [ ] Stress test generates samples (confirms profiler runs)
- [ ] Samples appear in Datadog UI (confirms upload works)
- [ ] Lock acquire/release events present (confirms lock profiling enabled)
- [ ] Variable names in samples (THIS IS WHAT WE'RE TESTING)

If first 4 pass but 5th fails:
- [ ] Check local pprof files for lock names
- [ ] Compare collector config between unit tests and production
- [ ] Enable debug logging
- [ ] Check frame inspection logic
- [ ] Compare pprof format with backend expectations

## Expected Timeline

1. **Immediate:** Stress test completes, shows summary
2. **30-60 seconds:** First samples arrive in backend
3. **2-3 minutes:** Samples visible in UI with time-series data
4. **5 minutes:** Full profile aggregated and available

## Success Criteria

✅ **Test passes if:**
- Lock samples show variable names like `user_session_lock`
- Different lock names are distinguishable
- Class member locks show attribute names
- Global locks show global variable names

❌ **Test fails if:**
- Only generic names like `threading.py:XXX`
- All locks have same name
- No way to distinguish between different locks
- Only memory addresses visible

## Files to Check

If debugging is needed:

1. **Collector implementation:**
   - `ddtrace/profiling/collector/_lock.py` - Name detection logic
   - `ddtrace/profiling/collector/threading.py` - RLock collector

2. **ddup interface:**
   - `ddtrace/internal/datadog/profiling/ddup.py` - Python interface
   - Native code - Actual serialization

3. **Configuration:**
   - `ddtrace/settings/profiling/config.py` - Lock profiling settings

4. **Tests:**
   - `tests/profiling_v2/collector/test_threading.py` - Unit tests
   - `tests/profiling/collector/lock_utils.py` - Test utilities
   - `tests/profiling/collector/pprof_utils.py` - pprof parsing

## Quick Test Command

```bash
# One-liner to run everything
cd /Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-2 && \
  export DD_AGENT_HOST="localhost" && \
  ./scripts/run_rlock_production_stress_test.sh 60 4 --yes
```

Then check: https://app.datadoghq.com/profiling?service=rlock-production-stress-test

