# Lock Production Test Suite - Quick Reference

## What's This?

Test suite for `threading.Lock()` on the main branch to verify lock name detection in production.

**Sister suite:** RLock tests are in commit d979a1bd4a3b552b6abd51396e913d2829962c5c

## Files Created

1. **`lock_production_stress_test.py`** - Main test (6 phases, 120s)
2. **`lock_debug_stress_test.py`** - Debug with local pprof analysis
3. **`run_lock_production_stress_test.sh`** - Helper runner
4. **`LOCK_STRESS_TEST_README.md`** - Full documentation
5. **`LOCK_TEST_SUITE_SUMMARY.md`** - This file

## Quick Start

```bash
# Option 1: Agent mode (recommended)
export DD_AGENT_HOST="localhost"
./scripts/run_lock_production_stress_test.sh

# Option 2: Debug mode (to see what's captured locally)
export DD_AGENT_HOST="localhost"
python3 scripts/lock_debug_stress_test.py 30 4

# Option 3: Quick test
python3 scripts/lock_production_stress_test.py 30 4
```

## What Should Happen

### ✅ Success (What We Want to See)

In Datadog UI, lock names should show variable names:
- `lock_production_stress_test.py:141:request_lock`
- `lock_production_stress_test.py:68:user_session_lock`
- `lock_production_stress_test.py:77:cache_read_lock`

### ❌ Problem (What You're Currently Seeing?)

Only generic names:
- `threading.py:123`
- `<threading.Lock object at 0x...>`

## Test Phases

The test runs 6 phases (no reentrant phase since Lock doesn't support it):

1. Local named locks (`request_lock`, `response_lock`)
2. Global named locks (`global_database_lock`)
3. Class member locks (`user_session_lock`, `cache_lock`)
4. Nested class locks (accessing locks in nested objects)
5. Private member locks (`_internal_lock`)
6. High contention (wait times with named locks)

## Debug Workflow

```bash
# 1. Verify unit tests work
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingLockCollector -v

# 2. Run debug version to see local pprof
python3 scripts/lock_debug_stress_test.py 30 4

# 3. Check what debug found
# If local pprof has names → collector works, issue is in upload/backend
# If local pprof has NO names → collector config issue

# 4. Check Datadog UI
# https://app.datadoghq.com/profiling
# Filter: service=lock-debug-stress-test, env=debug

# 5. Compare local vs UI
```

## Viewing Results

1. Wait 2-3 minutes after test completes
2. Go to https://app.datadoghq.com/profiling
3. Filter by:
   - **Service:** `lock-production-stress-test`
   - **Environment:** `stress-test`
4. Look at Lock profile type
5. Click on samples to see lock names

## Commands

```bash
# Unit tests
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingLockCollector -v

# Quick 30s test
python3 scripts/lock_production_stress_test.py 30 4

# Standard 2min test
./scripts/run_lock_production_stress_test.sh

# Long 5min test
./scripts/run_lock_production_stress_test.sh 300 16

# Debug mode
python3 scripts/lock_debug_stress_test.py 30 4

# No confirmation prompt
./scripts/run_lock_production_stress_test.sh 120 8 --yes
```

## Comparison: Lock vs RLock Suites

| Aspect | Lock Suite (main) | RLock Suite (d979a1bd) |
|--------|-------------------|------------------------|
| **Lock Type** | `threading.Lock()` | `threading.RLock()` |
| **Collector** | `ThreadingLockCollector` | `ThreadingRLockCollector` |
| **Phases** | 6 (no reentrant) | 7 (includes reentrant) |
| **Service** | `lock-production-stress-test` | `rlock-production-stress-test` |
| **Files** | `lock_*.py`, `run_lock_*.sh` | `rlock_*.py`, `run_rlock_*.sh` |
| **Location** | Current branch (main) | Commit d979a1bd |

## Key Differences from Unit Tests

**Unit Tests:**
- Use collector directly
- Write local pprof files
- Auto-parse and assert
- **Known to pass** ✅

**This Stress Test:**
- Use `ddtrace.profiling.auto` (production)
- Upload to Datadog backend
- Manual UI inspection
- Tests full pipeline

## Troubleshooting

### No samples appearing?

```bash
# Check agent
curl http://localhost:8126/info

# Check env vars
env | grep DD_

# Try debug mode
python3 scripts/lock_debug_stress_test.py 30 4
```

### Samples but no lock names?

Run debug mode to diagnose:

```bash
python3 scripts/lock_debug_stress_test.py 30 4
```

The script will tell you if issue is in:
- Collector (no names in local pprof)
- Upload/backend (names in local but not UI)

## Expected Lock Names

The test creates locks with these names:

**Local:**
- `request_lock`
- `response_lock`

**Global:**
- `global_user_session_lock`
- `global_cache_lock`
- `global_database_lock`

**Class Members:**
- `user_session_lock`
- `user_cache_lock`
- `login_attempts_lock`
- `cache_read_lock`
- `cache_write_lock`
- `connection_pool_lock`
- `query_lock`

**Private:**
- `_internal_lock` (or `_CacheManager__internal_lock`)

## Related Files

- **This suite:** `scripts/lock_*.py` on main branch
- **RLock suite:** Commit d979a1bd4a3b552b6abd51396e913d2829962c5c
- **Unit tests:** `tests/profiling_v2/collector/test_threading.py`
- **Collector:** `ddtrace/profiling/collector/_lock.py`

## Next Steps

1. **Run the test:**
   ```bash
   export DD_AGENT_HOST="localhost"
   ./scripts/run_lock_production_stress_test.sh
   ```

2. **Wait 2-3 minutes** for samples

3. **Check UI** for lock names

4. **If missing**, run debug:
   ```bash
   python3 scripts/lock_debug_stress_test.py 30 4
   ```

5. **Compare** with unit test expectations

---

**Created:** For `threading.Lock()` testing on main branch  
**Sister Suite:** RLock suite in commit d979a1bd  
**Purpose:** Verify lock name detection works in production

