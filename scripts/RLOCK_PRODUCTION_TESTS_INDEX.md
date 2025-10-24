# RLock Production Test Suite - Index

Quick reference for all RLock production testing tools.

## 📋 Problem Statement

**Issue:** Lock variable names (e.g., `user_session_lock`) appear correctly in unit tests but may only show as generic names (e.g., `threading.py:123`) in Datadog production UI.

**Goal:** Confirm whether lock name detection works end-to-end in production or if names are lost somewhere in the pipeline (collector → agent/API → backend → UI).

## 🎯 Quick Start

### Choose Your Test:

1. **First Time? Start Here:**
   ```bash
   export DD_AGENT_HOST="localhost"
   ./scripts/run_rlock_production_stress_test.sh
   ```

2. **Need Debugging? Use This:**
   ```bash
   export DD_AGENT_HOST="localhost"
   python3 scripts/rlock_debug_stress_test.py 30 4
   ```

3. **Just Want to Verify Unit Tests Work:**
   ```bash
   pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector -v
   ```

## 📁 Files in This Suite

### Executable Scripts

| File | Purpose | Duration | Output |
|------|---------|----------|--------|
| **`rlock_production_stress_test.py`** | Main stress test for production | 120s (configurable) | Datadog UI |
| **`rlock_debug_stress_test.py`** | Debug version with local analysis | 30s (configurable) | Local pprof + UI |
| **`run_rlock_production_stress_test.sh`** | Helper script with checks | Same as main | Pretty output |

### Documentation

| File | Contents |
|------|----------|
| **`RLOCK_STRESS_TEST_SUMMARY.md`** | Quick reference guide (start here) |
| **`RLOCK_PRODUCTION_STRESS_TEST_README.md`** | Comprehensive documentation |
| **`RLOCK_TEST_COMPARISON.md`** | Unit tests vs production comparison |
| **`RLOCK_PRODUCTION_TESTS_INDEX.md`** | This file |

### Reference Files

| File | Purpose |
|------|---------|
| `tests/profiling_v2/collector/test_threading.py` | Unit tests (known to work) |
| `ddtrace/profiling/collector/_lock.py` | Lock collector implementation |

### Existing Demo Scripts (For Comparison)

| File | Purpose |
|------|---------|
| `rlock_stress_test.py` | Original stress test |
| `rlock_simple_demo.py` | Simple demo |
| `rlock_heavy_contention.py` | Heavy contention demo |

## 🚀 Usage Scenarios

### Scenario 1: "I want to verify lock names work in production"

```bash
# Step 1: Run the stress test
export DD_AGENT_HOST="localhost"
./scripts/run_rlock_production_stress_test.sh

# Step 2: Wait 2-3 minutes

# Step 3: Check UI
# Go to: https://app.datadoghq.com/profiling
# Filter: service=rlock-production-stress-test, env=stress-test
# Look for lock names in samples
```

**Expected:** Lock names like `user_session_lock`, `cache_lock`, etc.

### Scenario 2: "Lock names aren't showing up, help me debug"

```bash
# Run debug version to see local pprof analysis
export DD_AGENT_HOST="localhost"
python3 scripts/rlock_debug_stress_test.py 30 4

# This will:
# 1. Run workloads
# 2. Write local pprof files
# 3. Parse and show lock names found
# 4. Tell you if issue is in collector or upload pipeline
```

### Scenario 3: "I want to compare with unit tests"

```bash
# Step 1: Run unit tests
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector::test_class_member_lock -v

# Step 2: Run production test with similar patterns
python3 scripts/rlock_production_stress_test.py 60 4

# Step 3: Compare results
# Unit tests: Check test output (should pass)
# Production: Check Datadog UI (should show lock names)
```

### Scenario 4: "I want to run a quick smoke test"

```bash
# 30 second test with 4 workers
python3 scripts/rlock_production_stress_test.py 30 4
```

### Scenario 5: "I want to run a long, comprehensive test"

```bash
# 10 minute test with 16 workers
./scripts/run_rlock_production_stress_test.sh 600 16
```

## 📊 What Each Test Checks

### Main Stress Test (`rlock_production_stress_test.py`)

Tests these lock patterns:
- ✅ Local named locks (`request_lock`, `response_lock`)
- ✅ Global named locks (`global_database_lock`)
- ✅ Class member locks (`user_session_lock`, `cache_lock`)
- ✅ Nested class locks (accessing parent/child locks)
- ✅ Private member locks (`_internal_lock`)
- ✅ Anonymous locks (no variable name)
- ✅ Reentrant patterns (RLock-specific reentrancy)

Runs 7 phases, each testing a different pattern.

### Debug Test (`rlock_debug_stress_test.py`)

Same patterns as main test, but:
- ✅ Writes local pprof files
- ✅ Parses pprof after completion
- ✅ Shows what lock names were captured
- ✅ Helps identify where names might be lost

Runs 3 simplified workload types.

### Unit Tests (`test_threading.py`)

Reference implementation:
- ✅ Tests all lock name scenarios
- ✅ Uses local pprof files
- ✅ Asserts lock names are present
- ✅ Known to pass

## 🔍 Interpreting Results

### ✅ Success

**What you see:**
- Unit tests pass
- Debug script shows lock names in local pprof
- Datadog UI shows lock names like `user_session_lock`
- Can distinguish different locks

**Conclusion:** Lock name detection works end-to-end!

### ⚠️ Partial Success

**What you see:**
- Unit tests pass
- Debug script shows lock names in local pprof
- Datadog UI shows generic names like `threading.py:123`

**Conclusion:** Collector works, but names are lost in upload/backend/UI pipeline.

**Next steps:**
- Check pprof format compatibility
- Check agent/backend processing
- Check UI rendering

### ❌ Failure

**What you see:**
- Unit tests pass
- Debug script shows NO lock names in local pprof
- Datadog UI shows generic names

**Conclusion:** Production configuration differs from unit test environment.

**Next steps:**
- Compare collector configuration
- Check environment variables
- Check frame inspection
- Check if variables are optimized away

### 🔴 Critical Failure

**What you see:**
- Unit tests fail

**Conclusion:** Collector is broken.

**Next steps:**
- Fix collector first
- Then retry production tests

## 📖 Documentation Roadmap

1. **Start here:** `RLOCK_STRESS_TEST_SUMMARY.md` - Quick overview
2. **Detailed info:** `RLOCK_PRODUCTION_STRESS_TEST_README.md` - Complete guide
3. **Comparison:** `RLOCK_TEST_COMPARISON.md` - Unit tests vs production
4. **This file:** `RLOCK_PRODUCTION_TESTS_INDEX.md` - Navigation

## 🔧 Configuration

### Required (Choose One)

**Option A: Local Agent**
```bash
export DD_AGENT_HOST="localhost"
export DD_TRACE_AGENT_PORT="8126"  # optional, defaults to 8126
```

**Option B: Agentless**
```bash
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"  # or datadoghq.eu, us3.datadoghq.com, etc.
```

### Optional

```bash
export DD_SERVICE="custom-service-name"
export DD_ENV="custom-env"
export DD_VERSION="1.0.0"
```

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| Script won't run | `chmod +x scripts/*.sh` |
| No samples in UI | Check agent is running, wait 2-3 minutes |
| Samples but no lock names | Run debug script to isolate issue |
| Python errors | Check ddtrace is installed: `pip install ddtrace` |
| Import errors | Run from repo root directory |

## 📞 Support

1. Read `RLOCK_STRESS_TEST_SUMMARY.md` for quick help
2. Read `RLOCK_PRODUCTION_STRESS_TEST_README.md` for detailed help
3. Run debug script to diagnose issues
4. Compare with unit tests to isolate problem
5. File bug report with debug output

## 🎓 Understanding the Test Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        Unit Tests                            │
│  Lock → Collector → ddup → pprof file → parse → assert     │
│  ✅ Known to work                                            │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Debug Stress Test                         │
│  Lock → Collector → ddup → pprof file (local) → parse       │
│                           └─→ Agent/API → Backend → UI      │
│  ❓ Does production config work?                             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                Production Stress Test                        │
│  Lock → Collector → ddup → Agent/API → Backend → UI         │
│  ❓ Does full pipeline preserve lock names?                  │
└─────────────────────────────────────────────────────────────┘
```

## 🎯 Decision Tree

```
Start
  │
  ├─→ [Never run RLock tests before?]
  │   └─→ YES: Read RLOCK_STRESS_TEST_SUMMARY.md first
  │   └─→ NO: Continue
  │
  ├─→ [Want to verify production works?]
  │   └─→ YES: Run ./scripts/run_rlock_production_stress_test.sh
  │   └─→ NO: Continue
  │
  ├─→ [Lock names not showing up?]
  │   └─→ YES: Run scripts/rlock_debug_stress_test.py
  │   └─→ NO: Continue
  │
  ├─→ [Need detailed docs?]
  │   └─→ YES: Read RLOCK_PRODUCTION_STRESS_TEST_README.md
  │   └─→ NO: Continue
  │
  └─→ [Want to compare with unit tests?]
      └─→ YES: Read RLOCK_TEST_COMPARISON.md
      └─→ NO: You're all set!
```

## ✨ Key Features

- 🎯 **Mirrors unit tests** - Same patterns, production environment
- 🔍 **Debug mode** - Local pprof analysis to isolate issues
- 📊 **Comprehensive** - Tests all lock name scenarios
- 🚀 **Easy to run** - Helper scripts with good UX
- 📖 **Well documented** - Multiple levels of documentation
- 🛠️ **Diagnostic** - Helps identify where names are lost

## 📝 Quick Command Reference

```bash
# Verify unit tests work
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingRLockCollector -v

# Run main stress test (agent mode)
export DD_AGENT_HOST="localhost"
./scripts/run_rlock_production_stress_test.sh

# Run main stress test (agentless mode)
export DD_API_KEY="your-key" DD_SITE="datadoghq.com"
./scripts/run_rlock_production_stress_test.sh

# Run debug version
python3 scripts/rlock_debug_stress_test.py 30 4

# Quick test
python3 scripts/rlock_production_stress_test.py 30 4

# Long test
./scripts/run_rlock_production_stress_test.sh 600 16

# Auto-accept
./scripts/run_rlock_production_stress_test.sh 120 8 --yes
```

---

**Version:** 1.0  
**Created:** October 2025  
**Purpose:** Verify RLock lock name detection in production pipeline

