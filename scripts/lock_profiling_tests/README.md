# Lock & RLock Profiling Test Suite

Comprehensive test suite for verifying lock name detection in Datadog profiling for both `threading.Lock()` and `threading.RLock()`.

## 🚀 Quick Start

### For Testing (Simplest - Agentless Mode)

```bash
# Set up environment (one time)
export DD_API_KEY="your-datadog-api-key"
export DD_SITE="datadoghq.com"  # or datadoghq.eu, etc.

# Run a quick test (pick one)
python3 scripts/lock_profiling_tests/rlock_simple_demo.py             # 30s RLock demo
python3 scripts/lock_profiling_tests/rlock_heavy_contention.py        # 60s, best for demos!

# View results after 30 seconds at:
# https://app.datadoghq.com/profiling
```

### For Production Testing (Agent Mode)

```bash
# Use your existing agent (API key in /etc/datadog-agent/datadog.yaml)
export DD_AGENT_HOST="localhost"
export DD_TRACE_AGENT_PORT="8126"  # or your custom port

# Run tests
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type RLock
```

## 📁 Available Test Scripts

| Script | Duration | Purpose |
|--------|----------|---------|
| `production_stress_test.py` | 120s | Comprehensive stress test (Lock/RLock) - 6-7 phases |
| `rlock_simple_demo.py` | 30s | Quick RLock verification test |
| `rlock_heavy_contention.py` | 60s | **Best for demos** - extreme RLock contention |

## 🎯 What These Tests Verify

### Expected Lock Names in Datadog UI

When tests run successfully, you should see lock names with variable identifiers:
- ✅ `rlock_simple_demo.py:35:counter_lock`
- ✅ `lock_production_stress_test.py:141:request_lock`
- ✅ `rlock_heavy_contention.py:50:BOTTLENECK`

### Problem: Generic Names

If lock name detection isn't working, you'll only see:
- ❌ `threading.py:123`
- ❌ `<threading.Lock object at 0x...>`

### Test Coverage

All tests verify these lock patterns (from unit tests):
- **Local named locks** - `request_lock`, `response_lock`
- **Global locks** - `global_database_lock`
- **Class member locks** - `user_session_lock`, `cache_lock`
- **Nested class locks** - locks in nested object hierarchies
- **Private member locks** - `_internal_lock` (mangled names)
- **Anonymous locks** - locks without variable names
- **Reentrant patterns** - (RLock only) same thread acquiring multiple times

## 🔧 Configuration

### Agent Mode (Recommended for Production)

**How it works:**
```
Your App → Local Datadog Agent (localhost:8126) → Datadog Backend
```

**Setup:**
```bash
export DD_AGENT_HOST="localhost"
export DD_TRACE_AGENT_PORT="8126"  # optional, defaults to 8126

# Test agent connectivity
curl http://localhost:8126/info
```

**Benefits:**
- ✅ API key in agent config (one place)
- ✅ Better performance (local network)
- ✅ Agent handles retries and buffering
- ✅ Recommended for production apps

### Agentless Mode (Simplest for Testing)

**How it works:**
```
Your App → Datadog Backend (direct HTTPS)
```

**Setup:**
```bash
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"  # or datadoghq.eu, us3.datadoghq.com, etc.
```

**Benefits:**
- ✅ Super simple - just 2 env vars
- ✅ No agent setup needed
- ✅ Perfect for quick tests and demos

**Sites:**
- US: `datadoghq.com`
- EU: `datadoghq.eu`
- US3: `us3.datadoghq.com`
- US5: `us5.datadoghq.com`
- AP1: `ap1.datadoghq.com`

## 📖 Detailed Usage

### Demo Mode (Recommended First)

```bash
# 1. Set environment
export DD_API_KEY="your-key"
export DD_SITE="datadoghq.com"

# 2. Run interactive menu
./scripts/lock_profiling_tests/run_rlock_demos.sh

# OR run directly
python3 scripts/lock_profiling_tests/rlock_heavy_contention.py

# 3. Wait 30 seconds, then check UI
# https://app.datadoghq.com/profiling
# Filter by service: rlock-heavy-contention
```


### Production Stress Test

Comprehensive stress test with production upload:

```bash
export DD_AGENT_HOST="localhost"

# Test Lock
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type Lock --duration 120

# Test RLock
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type RLock --duration 120

# Test both
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type both --duration 120

# Check UI after 2-3 minutes at https://app.datadoghq.com/profiling
```

## 🐛 Troubleshooting

### No Samples Appearing in UI?

**1. Check configuration:**
```bash
# What's set?
env | grep DD_

# If nothing, set either:
export DD_AGENT_HOST="localhost"  # Agent mode
# OR
export DD_API_KEY="key" DD_SITE="datadoghq.com"  # Agentless
```

**2. Test agent connectivity (if using agent):**
```bash
curl http://localhost:8126/info
```

**3. Enable debug logging:**
```bash
DD_TRACE_DEBUG=true python3 scripts/lock_profiling_tests/rlock_simple_demo.py
```

### Samples But No Lock Names?

**1. Check unit tests:**
```bash
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingLockCollector -v
```

**2. Review lock name detection:**
See the "Understanding Lock Name Detection" section below for how lock names are captured.

## 📊 Understanding the Results

### In Datadog UI

1. Go to https://app.datadoghq.com/profiling
2. Filter by service name (shown in script output)
3. Select Lock profile type
4. Look at samples - should show lock names

### Key Metrics to Check

- **lock-acquire** - Shows where locks are acquired
- **lock-release** - Shows hold duration
- **lock-acquire-wait** - Shows contention/wait time

### RLock-Specific Patterns

For RLock tests, also verify:
- Reentrant acquisitions (same thread acquiring multiple times)
- Nested lock patterns
- Wait times when multiple threads compete

## 🧪 Test Structure Comparison

### Unit Tests (Known to Work)
- Location: `tests/profiling_v2/collector/test_threading.py`
- Use `ddup.config()` with local output
- Parse pprof files locally
- Assert lock names are present
- ✅ These pass, confirming collector works

### Production Stress Tests (This Suite)
- Location: `scripts/lock_*.py`, `scripts/rlock_*.py`
- Use `ddtrace.profiling.auto` (production setup)
- Upload to Datadog backend
- Manual verification in UI
- ✅ Tests full pipeline: collector → agent/API → backend → UI

## 🔍 What We're Testing

**The core question:** Are lock variable names preserved through the entire pipeline?

```
Lock Creation
    ↓
Collector (_lock.py) - captures variable name via frame inspection
    ↓
ddup (native profiler) - serializes to pprof format
    ↓
Agent or Direct Upload - sends to Datadog
    ↓
Datadog Backend - stores and processes
    ↓
Datadog UI - displays to user
```

**What can go wrong:**
- Frame inspection fails → no names captured
- Serialization loses names → not in pprof
- Upload issues → not reaching backend
- Backend processing → names lost
- UI display → names not shown

**Debug scripts help isolate the issue** by checking local pprof files.

## 📝 Quick Command Reference

```bash
# Unit tests (verify collector works)
pytest tests/profiling_v2/collector/test_threading.py::TestThreadingLockCollector -v

# Quick RLock demo
python3 scripts/lock_profiling_tests/rlock_simple_demo.py

# Best demo (high contention)
python3 scripts/lock_profiling_tests/rlock_heavy_contention.py

# Full stress test
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type Lock --duration 120
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type RLock --duration 120
python3 scripts/lock_profiling_tests/production_stress_test.py --lock-type both --duration 120
```

## 🎓 Understanding Lock Name Detection

Lock names are captured via frame inspection in `ddtrace/profiling/collector/_lock.py`:

```python
def _maybe_update_self_name(self) -> None:
    # Walk back through call stack to find where lock is used
    frame = sys._getframe(3)
    
    # Look in locals first, then globals
    self._self_name = self._find_self_name(frame.f_locals) or \
                      self._find_self_name(frame.f_globals)

def _find_self_name(self, var_dict: Dict[str, Any]) -> Optional[str]:
    # Search for variable that references this lock object
    for name, value in var_dict.items():
        if value is self:
            return name
        # Also check object attributes
        if config.lock.name_inspect_dir:
            for attribute in dir(value):
                if getattr(value, attribute) is self:
                    return attribute
    return None
```

**Key insight:** Lock name = variable name found in the frame where lock is used.

**Important note:** As of testing, the pprof label is `"lock name"` (with space), not `"lock-name"` (with hyphen).

## 📚 Related Files and Tools

### Source Code
- **Lock collector:** `ddtrace/profiling/collector/_lock.py`
- **Collector settings:** `ddtrace/settings/profiling/config.py`

### Testing
- **Unit tests:** `tests/profiling_v2/collector/test_threading.py`
- **Test utilities:** `tests/profiling/collector/lock_utils.py`
- **pprof parsing:** `tests/profiling/collector/pprof_utils.py`

---

## 📋 Summary of Lock Profiling Test Tools

| Tool | Location | Purpose | Best For |
|------|----------|---------|----------|
| **Production Stress Test** | `scripts/lock_profiling_tests/production_stress_test.py` | Validate lock names in production | End-to-end validation |
| **Unit Tests** | `tests/profiling_v2/collector/test_threading.py` | Pytest collector tests | CI/CD validation |
| **Demo Scripts** | `scripts/lock_profiling_tests/rlock_*.py` | Quick demos | Demonstrations |

---

**Created:** For testing Lock and RLock profiling on main branch  
**Purpose:** Verify lock variable names flow correctly through production pipeline  
**Maintainer:** DataDog APM team

