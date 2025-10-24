# RLock Production Stress Test

## Purpose

This stress test verifies that RLock variable names (e.g., `user_session_lock`, `cache_lock`) are correctly captured and sent to Datadog production/agent, not just in unit tests.

**Problem Being Investigated:**
Unit tests in `tests/profiling_v2/collector/test_threading.py` correctly detect and assert lock names, but when running application code with production profiler, only low-level threading library lock names appear in the profiling UI (e.g., `threading.py:123` instead of `user_session_lock`).

**Goal:**
Confirm whether lock name detection works in production or if there's a bug/configuration issue that causes names to be lost between the profiler and the Datadog backend.

## Test Structure

This test combines:
1. **Unit test patterns** from `tests/profiling_v2/collector/test_threading.py`:
   - Local named locks (`request_lock`, `response_lock`)
   - Global named locks (`global_user_session_lock`)
   - Class member locks (`user_session_lock`, `cache_lock`)
   - Nested class locks
   - Private member locks (`_internal_lock`)
   - Anonymous locks (no variable name)
   - Reentrant lock patterns (RLock-specific)

2. **Production upload mechanism** from existing stress tests:
   - Uses `ddtrace.profiling.auto` for automatic profiler startup
   - Supports both agent and agentless upload modes
   - Configurable duration and worker count

3. **High intensity**:
   - Multiple concurrent workers per phase
   - Multiple test phases covering different lock patterns
   - Sufficient contention to generate meaningful samples

## Expected Lock Names

When viewing the profiling data in the Datadog UI, you should see lock names like:

### Local Locks
- `request_lock`
- `response_lock`

### Global Locks
- `global_user_session_lock`
- `global_cache_lock`
- `global_database_lock`

### Class Member Locks
- `user_session_lock`
- `user_cache_lock`
- `login_attempts_lock`
- `cache_read_lock`
- `cache_write_lock`
- `connection_pool_lock`
- `query_lock`

### Private Locks
- `_internal_lock` (or mangled: `_CacheManager__internal_lock`)

### Anonymous Locks
- (no variable name, just `filename:line`)

## ⚠️ Problem Indicator

If you **only** see low-level threading lock names like:
- `<threading.RLock object at 0x...>`
- `threading.py:XXX`

Then the lock name detection is **NOT working correctly** in production!

## Usage

### Quick Start

```bash
# Using local Datadog Agent
export DD_AGENT_HOST="localhost"
export DD_TRACE_AGENT_PORT="8126"
./scripts/run_rlock_production_stress_test.sh

# Using agentless mode (direct to Datadog)
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"
./scripts/run_rlock_production_stress_test.sh
```

### Custom Duration and Workers

```bash
# Run for 5 minutes with 16 workers per phase
./scripts/run_rlock_production_stress_test.sh 300 16

# Skip confirmation prompt
./scripts/run_rlock_production_stress_test.sh 120 8 --yes
```

### Direct Python Execution

```bash
# With agent
export DD_AGENT_HOST="localhost"
python3 scripts/rlock_production_stress_test.py 120 8

# With agentless
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"
python3 scripts/rlock_production_stress_test.py 120 8
```

## Test Phases

The stress test runs 7 phases, each focusing on a different lock pattern:

1. **Phase 1: Local Named Locks**
   - Tests locally scoped locks with variable names
   - Should show `request_lock`, `response_lock`

2. **Phase 2: Global Named Locks**
   - Tests module-level global locks
   - Should show `global_user_session_lock`, etc.

3. **Phase 3: Class Member Locks**
   - Tests locks as class instance attributes
   - Should show `user_session_lock`, `cache_read_lock`, etc.

4. **Phase 4: Nested Class Locks**
   - Tests locks in nested object hierarchies
   - Should show locks from both parent and nested objects

5. **Phase 5: Reentrant Lock Patterns**
   - Tests RLock-specific reentrancy (same thread acquires multiple times)
   - Should show clear reentrant acquisition patterns

6. **Phase 6: Private Member Locks**
   - Tests private/mangled attribute names
   - Should show `_internal_lock` or `_ClassName__internal_lock`

7. **Phase 7: High Contention (Named Locks)**
   - Tests high contention scenarios with named locks
   - Should show wait times with proper lock names

## Viewing Results

1. Go to https://app.datadoghq.com/profiling
2. Filter by:
   - **Service:** `rlock-production-stress-test`
   - **Environment:** `stress-test`
3. Look at the Lock profile type
4. Check sample details for lock names

### What to Check

✅ **Success Indicators:**
- Lock samples show variable names (e.g., `user_session_lock`)
- Lock acquire/release events have meaningful names
- Lock wait time samples include variable names
- Reentrant patterns are visible

❌ **Failure Indicators:**
- Only generic names like `threading.py:123`
- Lock names show memory addresses like `<threading.RLock object at 0x...>`
- No variable names visible in any samples

## Comparison with Unit Tests

### Unit Tests (`tests/profiling_v2/collector/test_threading.py`)
- Use `ddup.config()` with local output files
- Parse pprof files locally using `pprof_utils.parse_newest_profile()`
- Validate lock names with assertions
- **These tests pass, confirming lock name detection works in the collector**

### This Stress Test
- Uses `ddtrace.profiling.auto` (production mode)
- Uploads to Datadog backend via agent or agentless
- Manual validation in Datadog UI
- **Tests the full pipeline: collector → exporter → agent/backend → UI**

## Implementation Details

The test mirrors unit test structures:

```python
# Similar to test_lock_events
def test_local_named_locks(worker_id: int, iterations: int):
    for i in range(iterations):
        request_lock = threading.RLock()
        with request_lock:
            time.sleep(0.0005)
```

```python
# Similar to test_class_member_lock  
class UserManager:
    def __init__(self, lock_class):
        self.user_session_lock = lock_class()
    
    def authenticate_user(self, user_id: str, iterations: int):
        with self.user_session_lock:
            time.sleep(0.001)
```

```python
# Similar to test_global_locks
global_user_session_lock = threading.RLock()

def test_global_locks(worker_id: int, iterations: int):
    global global_user_session_lock
    with global_user_session_lock:
        time.sleep(0.001)
```

## Lock Name Detection Mechanism

From `ddtrace/profiling/collector/_lock.py`:

```python
def _maybe_update_self_name(self) -> None:
    if self._self_name is not None:
        return
    frame = sys._getframe(3)
    
    # Look in locals first, then globals
    self._self_name = self._find_self_name(frame.f_locals) or self._find_self_name(frame.f_globals)

def _find_self_name(self, var_dict: Dict[str, Any]) -> Optional[str]:
    for name, value in var_dict.items():
        if value is self:
            return name
        if config.lock.name_inspect_dir:
            for attribute in dir(value):
                if getattr(value, attribute) is self:
                    return attribute
    return None
```

The collector:
1. Walks back through the call stack to find the frame where the lock is used
2. Looks in `f_locals` and `f_globals` for a variable that matches the lock object
3. Optionally inspects object attributes using `dir()` if `config.lock.name_inspect_dir` is enabled
4. Combines the variable name with the creation location

## Troubleshooting

### Lock names not appearing?

1. **Check configuration:**
   ```python
   from ddtrace.settings.profiling import config
   print(config.lock.name_inspect_dir)  # Should be True for class members
   ```

2. **Verify profiler is running:**
   - Check logs for "Datadog Profiler started"
   - Verify samples are being uploaded (agent logs or Datadog UI)

3. **Check sample volume:**
   - If there are very few samples, lock names might be sparse
   - Try running the test longer

4. **Compare with unit tests:**
   - Run unit tests to confirm the collector works in isolation
   - If unit tests pass but this fails, issue is in upload/backend pipeline

### No samples appearing at all?

1. **Agent mode issues:**
   - Check agent is running: `curl http://localhost:8126/info`
   - Check agent logs for profiling intake
   - Verify port (8126 for trace agent)

2. **Agentless mode issues:**
   - Verify API key is valid
   - Check site is correct (datadoghq.com, datadoghq.eu, etc.)
   - Check network connectivity

3. **Profiler not starting:**
   - Check for import errors in logs
   - Verify native extensions are compiled (`ddup.is_available`)

## Related Files

- **Unit tests:** `tests/profiling_v2/collector/test_threading.py`
- **Lock collector:** `ddtrace/profiling/collector/_lock.py`
- **Lock utilities:** `tests/profiling/collector/lock_utils.py`
- **Existing stress tests:**
  - `scripts/rlock_stress_test.py`
  - `scripts/rlock_simple_demo.py`
  - `scripts/rlock_heavy_contention.py`

## Configuration Options

All environment variables can be set before running:

```bash
# Profiling
export DD_PROFILING_ENABLED=1
export DD_PROFILING_LOCK_ENABLED=1
export DD_PROFILING_CAPTURE_PCT=100

# Service identification
export DD_SERVICE=rlock-production-stress-test
export DD_ENV=stress-test
export DD_VERSION=1.0.0

# Upload: Agent mode
export DD_AGENT_HOST=localhost
export DD_TRACE_AGENT_PORT=8126

# Upload: Agentless mode
export DD_API_KEY=your-api-key
export DD_SITE=datadoghq.com
```

## Next Steps After Running

1. Wait 2-3 minutes for samples to appear in UI
2. Navigate to profiling page and filter by service
3. Check Lock profile type
4. Click on samples to see details
5. Verify lock names appear in sample details
6. Compare with unit test expectations

If lock names are missing:
- Document findings
- Check if issue is in collector, exporter, or backend
- Compare raw pprof output with what appears in UI
- File bug report with evidence

