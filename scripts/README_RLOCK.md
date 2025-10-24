# 🔒 RLock Profiling for Production

Quick reference for demonstrating RLock profiling in production.

## 🚀 Quick Start

**Setup (2 seconds):**
```bash
export DD_API_KEY="your-datadog-api-key"
export DD_SITE="datadoghq.com"  # or datadoghq.eu, us3.datadoghq.com, etc.
```

**Run:**
```bash
# Interactive menu (easiest)
./scripts/run_rlock_demos.sh

# Or run directly:
python3 scripts/rlock_simple_demo.py
python3 scripts/rlock_heavy_contention.py  # Most dramatic results!
```

## 📁 Files Created

| File | Purpose | Size |
|------|---------|------|
| `rlock_simple_demo.py` | 30s quick verification | 2.6K |
| `rlock_stress_test.py` | Comprehensive stress test | 8.3K |
| `rlock_heavy_contention.py` | Maximum visibility demo | 6.4K |
| `run_rlock_demos.sh` | Interactive menu runner | 4.7K |
| `RLOCK_PROFILING_DEMO.md` | Full documentation | 7.1K |
| `RLOCK_TESTS_SUMMARY.md` | Testing checklist | 4.3K |

## ✅ What These Tests Demonstrate

### 1. Lock Acquire & Release
- Stack traces showing where locks are acquired
- Hold duration (time between acquire and release)
- Thread information

### 2. Lock Wait Times
- Contention visualization
- Time spent waiting to acquire
- Bottleneck identification

### 3. RLock Reentrancy
- Same thread acquiring lock multiple times
- Nested lock acquisitions (up to 3 levels)
- Different from regular Lock behavior

## 🎯 Quick Test Checklist

- [ ] Set `DD_API_KEY` and `DD_SITE` environment variables
- [ ] Run `python3 scripts/rlock_simple_demo.py`
- [ ] Wait 15-30 seconds after script completes
- [ ] Open https://app.datadoghq.com/profiling
- [ ] Filter by service: `rlock-simple-demo`
- [ ] Verify lock samples appear in flamegraph
- [ ] Look for acquire, release, and wait samples

## 🔧 Configuration

### Agentless Mode (Default - Simplest!)

Scripts use **agentless mode** by default - just 2 environment variables:

```bash
export DD_API_KEY="your-api-key"
export DD_SITE="datadoghq.com"  # or datadoghq.eu, us3.datadoghq.com, etc.
python3 scripts/rlock_simple_demo.py
```

**Benefits for testing:**
- ✅ Super simple - just 2 env vars
- ✅ No agent setup needed
- ✅ Works immediately
- ✅ Perfect for demos and quick tests

### Alternative: Using Local Agent

If you prefer to use your local Datadog agent (better for production):

```bash
export DD_AGENT_HOST="localhost"
# No need for DD_API_KEY - agent has it in datadog.yaml
python3 scripts/rlock_simple_demo.py
```

**Benefits for production:**
- ✅ Better performance
- ✅ Agent handles retries and buffering
- ✅ Centralized API key management

## 📊 Expected Results

### In Datadog UI
1. **Profile Type: Lock-Acquire**
   - Flamegraph showing acquisition points
   - Thread names and IDs
   - Lock names with file:line

2. **Profile Type: Lock-Release**
   - Hold duration samples
   - Release locations

3. **Profile Type: Lock-Wait**
   - Contention hotspots
   - Wait time distribution

## 🎨 Which Script to Use?

```
First time testing?          → rlock_simple_demo.py
Need production-like load?   → rlock_stress_test.py
Preparing a demo/presentation? → rlock_heavy_contention.py
Want easy menu?              → run_rlock_demos.sh
```

## 💡 Pro Tips

1. **For obvious samples**: Use `rlock_heavy_contention.py`
   - Creates extreme contention
   - Very visible in UI
   - Perfect for demos

2. **For realistic testing**: Use `rlock_stress_test.py`
   - Multiple workload phases
   - Configurable duration and workers
   - Production-representative

3. **For quick checks**: Use `rlock_simple_demo.py`
   - Fast 30-second test
   - Minimal setup
   - Good for CI/verification

## 🐛 Troubleshooting

### No samples in UI?
```bash
# 1. Verify API key is set
echo $DD_API_KEY  # Should show your key
echo $DD_SITE     # Should show datadoghq.com (or your site)

# 2. Run script with debug output
DD_TRACE_DEBUG=true python3 scripts/rlock_simple_demo.py 2>&1 | grep -i "profil"

# 3. Check for errors in output
python3 scripts/rlock_simple_demo.py 2>&1 | grep -i error

# 4. Wait longer (first upload can take 30-60s)

# 5. Try a different site if needed
export DD_SITE="datadoghq.eu"  # or us3.datadoghq.com, etc.
```

### Script won't run?
```bash
# Check Python version (need 3.8+)
python3 --version

# Install ddtrace if needed
pip install ddtrace

# Check for import errors
python3 -c "import ddtrace.profiling.auto"
```

## 📚 Documentation

- **Full guide**: `RLOCK_PROFILING_DEMO.md`
- **Testing checklist**: `RLOCK_TESTS_SUMMARY.md`
- **Source code**: `ddtrace/profiling/collector/_lock.py`

## 🎬 Example Session

```bash
$ ./scripts/run_rlock_demos.sh

Select a demo to run:
  1) Simple Demo (30s) - Quick verification
  2) Heavy Contention (60s) - Maximum visibility
  ...

Enter choice [1-6]: 2

Running Heavy Contention Demo...
============================================================
Service: rlock-heavy-contention
Environment: demo
...
✅ All threads completed!

View profiling data at:
  https://app.datadoghq.com/profiling
```

---

**Ready to push to prod!** 🚀

Use `rlock_heavy_contention.py` for maximum visibility in the Datadog UI.

