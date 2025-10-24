# Lock Production Stress Test Suite

## Overview

This is the `threading.Lock()` version of the stress test suite. It tests lock name detection for regular (non-reentrant) locks on the main branch.

**Sister Suite:** The `rlock_*` scripts test `threading.RLock()` and are committed in d979a1bd4a3b552b6abd51396e913d2829962c5c on a different branch.

## Problem Being Tested

Lock variable names (e.g., `user_session_lock`, `cache_lock`) should appear in Datadog profiling UI, but they may only show as generic names (e.g., `threading.py:123`).

This suite verifies whether lock name detection works for `threading.Lock()` in production.

## Files

| File | Purpose |
|------|---------|
| **`lock_production_stress_test.py`** | Main stress test - 6 phases, 120s default |
| **`lock_debug_stress_test.py`** | Debug version with local pprof analysis |
| **`run_lock_production_stress_test.sh`** | Helper runner with checks |
| **`LOCK_STRESS_TEST_README.md`** | This file |

## Quick Start

### Option 1: Using Local Agent

```bash
export DD_AGENT_HOST="localhost"
./scripts/run_lock_production_stress_test.sh
```

### Option 2: Using Agentless Mode

```bash
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"
./scripts/run_lock_production_stress_test.sh
```

### Option 3: Debug Mode (Recommended First)

```bash
export DD_AGENT_HOST="localhost"
python3 scripts/lock_debug_stress_test.py 30 4
```

## What It Tests

### Test Phases (6 total)

1. **Local Named Locks** - `request_lock`, `response_lock`
2. **Global Named Locks** - `global_database_lock`, `global_cache_lock`
3. **Class Member Locks** - `user_session_lock`, `cache_lock`
4. **Nested Class Locks** - Locks in nested object hierarchies
5. **Private Member Locks** - `_internal_lock` (mangled names)
6. **High Contention** - Named locks with high wait times

**Note:** No reentrant phase since `threading.Lock()` doesn't support reentrancy.

## Expected Results

### In Datadog UI (Success)

Lock names should appear like:
- `lock_production_stress_test.py:141:request_lock`
- `lock_production_stress_test.py:68:user_session_lock`
- `lock_production_stress_test.py:77:cache_read_lock`
- `lock_production_stress_test.py:159:global_database_lock`

### In Datadog UI (Failure)

If you only see:
- `threading.py:123`
- `<threading.Lock object at 0x...>`
- Generic names without variable identifiers

Then lock name detection is broken!

## Usage Examples

### Quick 30-Second Test

```bash
python3 scripts/lock_production_stress_test.py 30 4
```

### Standard 2-Minute Test

```bash
./scripts/run_lock_production_stress_test.sh 120 8
```

### Long 5-Minute Test

```bash
./scripts/run_lock_production_stress_test.sh 300 16
```

### Debug with Local Analysis

```bash
python3 scripts/lock_debug_stress_test.py 30 4
```

This will:
1. Run simplified workloads
2. Write local pprof files to `/tmp/lock_debug_stress_test.*`
3. Parse pprof and show captured lock names
4. Upload to production
5. Tell you where the issue is (collector vs pipeline)

## Debugging Workflow

### Step 1: Verify Unit Tests Work

```bash
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingLockCollector -v
```

If these fail, the collector is broken.

### Step 2: Run Debug Stress Test

```bash
export DD_AGENT_HOST="localhost"
python3 scripts/lock_debug_stress_test.py 30 4
```

Check the output - does it show good lock names in local pprof?

### Step 3: Check Datadog UI

Wait 2-3 minutes, then check:
- https://app.datadoghq.com/profiling
- Filter: service=`lock-debug-stress-test`, env=`debug`
- Look at Lock profile samples

### Step 4: Diagnose

| Local pprof | UI | Diagnosis |
|-------------|-----|-----------|
| ✅ Has names | ✅ Has names | Everything works! |
| ✅ Has names | ❌ Generic | Issue in upload/backend |
| ❌ No names | ❌ Generic | Issue in collector config |

## Comparison with Unit Tests

**Unit tests** (`tests/profiling_v2/collector/test_threading.py`):
- Use `ThreadingLockCollector` directly
- Write local pprof files
- Parse and assert lock names
- **Known to pass** ✅

**This stress test**:
- Uses `ddtrace.profiling.auto` (production setup)
- Uploads to Datadog backend
- Manual inspection in UI
- Tests the full pipeline

## Configuration

All set via environment variables:

```bash
# Required (choose one)
export DD_AGENT_HOST="localhost"              # Agent mode
# OR
export DD_API_KEY="key" DD_SITE="datadoghq.com"  # Agentless

# Optional
export DD_SERVICE="custom-name"
export DD_ENV="custom-env"
export DD_PROFILING_CAPTURE_PCT="100"
```

## Viewing Results

1. Wait 2-3 minutes after test completes
2. Go to https://app.datadoghq.com/profiling
3. Filter by:
   - Service: `lock-production-stress-test` (or `lock-debug-stress-test`)
   - Environment: `stress-test` (or `debug`)
4. Click on Lock profile type
5. Inspect samples for lock names

## Key Differences from RLock Suite

| Aspect | Lock Suite | RLock Suite |
|--------|-----------|-------------|
| Lock Type | `threading.Lock()` | `threading.RLock()` |
| Reentrancy | Not supported | Fully tested |
| Phases | 6 | 7 (includes reentrant) |
| Service Name | `lock-production-stress-test` | `rlock-production-stress-test` |
| Branch | main | Different branch (commit d979a1bd) |
| Collector | `ThreadingLockCollector` | `ThreadingRLockCollector` |

## Troubleshooting

### No samples in UI?

```bash
# Check agent
curl http://localhost:8126/info

# Check environment vars
env | grep DD_

# Try debug mode
python3 scripts/lock_debug_stress_test.py 30 4
```

### Samples but no lock names?

Run debug mode to see if local pprof has names:

```bash
python3 scripts/lock_debug_stress_test.py 30 4
```

If local has names but UI doesn't → upload/backend issue
If local doesn't have names → collector configuration issue

### Script errors?

```bash
# Make executable
chmod +x scripts/lock_*.py scripts/run_lock_*.sh

# Check Python
python3 --version

# Check ddtrace
pip list | grep ddtrace
```

## Code Structure

The test creates the same patterns as unit tests:

```python
# Local named locks
def test_local_named_locks(worker_id, iterations):
    request_lock = threading.Lock()
    with request_lock:  # Should capture "request_lock"
        time.sleep(0.0005)

# Class member locks
class UserManager:
    def __init__(self):
        self.user_session_lock = threading.Lock()
    
    def authenticate(self):
        with self.user_session_lock:  # Should capture "user_session_lock"
            time.sleep(0.001)

# Global locks
global_database_lock = threading.Lock()

def test_global_locks(worker_id, iterations):
    with global_database_lock:  # Should capture "global_database_lock"
        time.sleep(0.001)
```

## Related Files

- **Unit tests:** `tests/profiling_v2/collector/test_threading.py`
- **Collector:** `ddtrace/profiling/collector/_lock.py`
- **Test utilities:** `tests/profiling/collector/lock_utils.py`
- **RLock suite:** Committed in d979a1bd4a3b552b6abd51396e913d2829962c5c

## Command Quick Reference

```bash
# Unit tests (Lock)
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingLockCollector -v

# Quick test
python3 scripts/lock_production_stress_test.py 30 4

# Standard test
./scripts/run_lock_production_stress_test.sh

# Long test
./scripts/run_lock_production_stress_test.sh 300 16

# Debug mode
python3 scripts/lock_debug_stress_test.py 30 4

# No confirmation
./scripts/run_lock_production_stress_test.sh 120 8 --yes
```

## Success Criteria

✅ **Test succeeds if:**
- Unit tests pass
- Debug shows lock names in local pprof
- UI shows lock names with variable identifiers
- Different locks are distinguishable

❌ **Test fails if:**
- Only generic `threading.py:XXX` names
- All locks have same name
- No way to distinguish locks
- Only memory addresses visible

## Next Steps After Running

1. Check Datadog UI for lock names
2. If missing, run debug mode
3. Compare with unit test behavior
4. Document findings
5. File bug if names are missing

---

**Created:** For testing `threading.Lock()` on main branch
**Sister Suite:** RLock suite on different branch (d979a1bd)
**Purpose:** Verify lock name detection in production pipeline

