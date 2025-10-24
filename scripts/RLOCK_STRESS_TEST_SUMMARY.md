# RLock Production Stress Test - Summary

## What Was Created

This package includes tools to verify that RLock variable names are correctly captured and sent to Datadog production, not just in unit tests.

### Files Created

1. **`rlock_production_stress_test.py`** - Main stress test script
   - Mirrors unit test patterns from `tests/profiling_v2/collector/test_threading.py`
   - Tests local, global, class member, nested, private, and anonymous locks
   - Runs 7 phases with multiple workers
   - Uploads to production (agent or agentless)
   - Duration: configurable (default 120s)

2. **`run_rlock_production_stress_test.sh`** - Helper runner script
   - Checks environment configuration
   - Provides friendly prompts and confirmation
   - Handles both agent and agentless modes
   - Shows results and instructions

3. **`rlock_debug_stress_test.py`** - Debugging version
   - Writes BOTH local pprof files AND uploads to production
   - Parses local pprof files to show captured lock names
   - Helps identify where lock names might be lost in pipeline
   - Duration: configurable (default 30s)

4. **`RLOCK_PRODUCTION_STRESS_TEST_README.md`** - Full documentation
   - Detailed explanation of the problem
   - Expected vs actual lock names
   - Usage instructions
   - Troubleshooting guide
   - Lock name detection mechanism explanation

5. **`RLOCK_TEST_COMPARISON.md`** - Unit tests vs production comparison
   - Side-by-side comparison
   - Code pattern examples
   - Debugging steps
   - Investigation checklist

6. **`RLOCK_STRESS_TEST_SUMMARY.md`** - This file
   - Quick reference
   - How to run tests
   - What to look for

## Quick Start

### Option 1: Using Local Agent (Recommended for Local Testing)

```bash
cd /Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-2

# Start your Datadog agent first
export DD_AGENT_HOST="localhost"
export DD_TRACE_AGENT_PORT="8126"

# Run the stress test
./scripts/run_rlock_production_stress_test.sh
```

### Option 2: Using Agentless Mode (Direct to Datadog)

```bash
cd /Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-2

# Set your API key
export DD_API_KEY="your-api-key-here"
export DD_SITE="datadoghq.com"  # or datadoghq.eu, us3.datadoghq.com, etc.

# Run the stress test
./scripts/run_rlock_production_stress_test.sh
```

### Option 3: Debug Mode (Local + Production)

```bash
# This writes local pprof files AND uploads to production
export DD_AGENT_HOST="localhost"
python3 scripts/rlock_debug_stress_test.py 30 4
```

## What to Expect

### During Test Run

1. Script starts, shows configuration
2. Runs 7 test phases (or 3 in debug mode)
3. Each phase runs for ~17 seconds (or 10s in debug)
4. Shows progress and completion
5. Waits 10-15 seconds for final upload
6. Shows instructions for viewing in UI

### In Datadog UI

1. Go to https://app.datadoghq.com/profiling
2. Filter by:
   - Service: `rlock-production-stress-test` (or `rlock-debug-stress-test`)
   - Environment: `stress-test` (or `debug`)
3. Look at Lock profile type
4. Click on samples

### Expected Lock Names

You should see lock names like:
- `request_lock`
- `response_lock`
- `user_session_lock`
- `cache_lock`
- `global_database_lock`
- `auth_lock`
- `connection_pool_lock`
- `query_lock`

### Problem Indicators

If you only see:
- `threading.py:123`
- `<threading.RLock object at 0x...>`
- Generic names without variable names

Then lock name detection is NOT working in production!

## Comparison with Unit Tests

### Unit Tests (Known to Work)

```bash
# Run unit tests to confirm collector works
cd /Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-2
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector -v
```

These tests:
- Write local pprof files
- Parse them automatically
- Assert lock names are present
- **These PASS, confirming collector works in isolation**

### Stress Tests (Testing Full Pipeline)

These tests:
- Upload to Datadog backend
- Require manual UI inspection
- Test the complete pipeline: collector → agent/API → backend → UI
- **Confirms whether lock names survive the full pipeline**

## Workflow

### Step 1: Verify Unit Tests Pass

```bash
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector::test_class_member_lock -v
```

✅ If passes: Collector works in isolation

### Step 2: Run Debug Stress Test

```bash
export DD_AGENT_HOST="localhost"
python3 scripts/rlock_debug_stress_test.py 30 4
```

This will:
- Run simplified workloads
- Write local pprof files
- Upload to production
- Parse local pprof and show what was captured

✅ If local pprof shows good lock names: Collector works in production mode

### Step 3: Check Datadog UI

Wait 2-3 minutes, then check UI for lock names.

✅ If UI shows good lock names: Everything works!
❌ If UI shows generic names: Issue is in upload/backend/UI pipeline

### Step 4: Run Full Stress Test

If debugging shows everything works, run the full stress test for comprehensive validation:

```bash
./scripts/run_rlock_production_stress_test.sh 120 8
```

## Common Commands

### Short Test (30 seconds)

```bash
python3 scripts/rlock_production_stress_test.py 30 4
```

### Standard Test (2 minutes)

```bash
./scripts/run_rlock_production_stress_test.sh 120 8
```

### Long Test (5 minutes)

```bash
./scripts/run_rlock_production_stress_test.sh 300 16
```

### Debug Test with Local Analysis

```bash
python3 scripts/rlock_debug_stress_test.py 30 4
```

### Auto-accept (No Confirmation)

```bash
./scripts/run_rlock_production_stress_test.sh 60 4 --yes
```

## Troubleshooting

### No samples in UI?

1. Check agent is running: `curl http://localhost:8126/info`
2. Check agent logs for profiling intake
3. Verify environment variables are set
4. Wait longer (can take 2-3 minutes)

### Samples but no lock names?

1. Run debug version to check local pprof
2. If local has names but UI doesn't: backend/UI issue
3. If local doesn't have names: collector configuration issue
4. Check `DD_PROFILING_LOCK_ENABLED=1`

### Script won't run?

1. Check script is executable: `chmod +x scripts/*.sh`
2. Verify Python 3 is available: `python3 --version`
3. Check ddtrace is installed: `pip list | grep ddtrace`

## Files Overview

```
scripts/
├── rlock_production_stress_test.py          # Main stress test
├── rlock_debug_stress_test.py               # Debug version with local pprof
├── run_rlock_production_stress_test.sh      # Helper runner
├── RLOCK_PRODUCTION_STRESS_TEST_README.md   # Detailed docs
├── RLOCK_TEST_COMPARISON.md                 # Unit tests vs production
└── RLOCK_STRESS_TEST_SUMMARY.md             # This file

tests/profiling_v2/collector/
└── test_threading.py                         # Unit tests (reference)

Existing stress tests (for comparison):
├── rlock_stress_test.py                     # Original stress test
├── rlock_simple_demo.py                     # Simple demo
└── rlock_heavy_contention.py                # Heavy contention demo
```

## Key Differences from Existing Scripts

### Existing Scripts (rlock_stress_test.py, etc.)

- Focus on demonstrating RLock behavior
- Create contention and reentrancy
- Simple workload patterns
- Generic lock usage

### New Production Stress Test

- Focus on lock NAME detection
- Mirrors unit test patterns exactly
- Tests all lock name scenarios:
  - Local variables
  - Global variables  
  - Class members
  - Nested classes
  - Private members
  - Anonymous locks
- Validates production pipeline end-to-end

### Debug Script

- Diagnostic tool
- Writes local + production
- Parses and analyzes pprof
- Identifies pipeline issues

## Success Criteria

✅ **Test succeeds if:**
1. Unit tests pass
2. Debug script shows lock names in local pprof
3. Datadog UI shows lock names in samples
4. Lock names match variable names from code

❌ **Test fails if:**
1. Unit tests fail → Collector broken
2. No lock names in local pprof → Production config issue
3. Lock names in local but not UI → Upload/backend issue
4. Only generic names in UI → Name detection not working

## Next Steps After Running

1. **Document findings**
   - Screenshot UI showing lock names (or lack thereof)
   - Save debug output
   - Note any discrepancies

2. **If lock names are missing**
   - Run debug script to isolate issue
   - Compare with unit test environment
   - Check collector configuration
   - File bug report with evidence

3. **If lock names are present**
   - Great! Lock name detection works
   - Consider running longer stress tests
   - Monitor for consistency

## Related Documentation

- **Unit tests:** `tests/profiling_v2/collector/test_threading.py`
- **Collector code:** `ddtrace/profiling/collector/_lock.py`
- **Lock settings:** `ddtrace/settings/profiling/config.py`
- **Test utilities:** `tests/profiling/collector/lock_utils.py`

## Support

For questions or issues:
1. Check the README files
2. Compare with unit test behavior
3. Run debug script for diagnostics
4. Check Datadog documentation
5. File issue with reproduction steps

---

**Created:** $(date)
**Purpose:** Verify RLock name detection in production
**Issue:** Lock names visible in unit tests but potentially missing in production UI

