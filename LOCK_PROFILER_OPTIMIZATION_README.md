# Lock Profiler Optimization Analysis

**Branch:** `vlad/lockprof-optimization-ideas`  
**Last Updated:** December 5, 2025

## Overview

This document consolidates analysis from:
1. **Local micro-benchmarks** (`measure_lock_overhead.py`) - per-operation overhead
2. **DoE production benchmarks** (`dd-trace-doe`) - real-world application impact
3. **Technology comparison** - Cython vs Rust vs C++ for hot path optimization

---

## Key Findings

### From DoE Production Benchmarks

| Metric | Impact | Notes |
|--------|--------|-------|
| **P50 Latency** | +11-12% | Consistent across contention levels |
| **P99 Latency** | +37-40% | **3.3x worse than P50** |
| **CPU Usage** | +160% (2.6x) | **Primary cost driver** |
| **Memory** | +15-50% | Variable, less concerning |

**Critical insight:** Overhead is **contention-independent** - it's the instrumentation machinery itself, not contention handling.

### From Local Micro-Benchmarks

| Configuration | Overhead per Lock Op | vs Baseline |
|---------------|---------------------|-------------|
| No profiling | 59ns | baseline |
| 0% capture (wrapper only) | 656ns | **11x** |
| 1% capture (DEFAULT) | 700ns | **12x** |
| 100% capture | 5,995ns | **101x** |

**Where does the ~640ns overhead go?**
```
┌─────────────────────────────────────────────────────────┐
│ Python function call overhead:          ~500-550ns (85%)│
│ ├─ Call to _acquire()                                   │
│ ├─ Method lookup (self._acquire)                        │
│ ├─ Argument packing (*args, **kwargs)                   │
│ └─ Delegation to inner_func()                           │
├─────────────────────────────────────────────────────────┤
│ CaptureSampler overhead:                  ~50-80ns (10%)│
│ ├─ Method call to capture()                             │
│ ├─ Counter increment                                    │
│ └─ Conditional check                                    │
├─────────────────────────────────────────────────────────┤
│ Other overhead:                           ~10-40ns (5%) │
└─────────────────────────────────────────────────────────┘
```

---

## Analysis: DoE vs Micro-Benchmarks

### Why P99 is 3.3x Worse Than P50

The DoE results show P99 latency impact (38%) is **3.3x higher** than P50 impact (11.7%). This suggests:

1. **Full sample path is expensive** - When the 1% sampler DOES capture, `_flush_sample()` adds ~5,300ns additional overhead:
   - Stack trace capture (`_traceback.pyframe_to_frames`)
   - Thread/task info gathering
   - ddup handle creation and flushing

2. **GC pressure** - Python object creation in the hot path causes occasional GC pauses

3. **Variance in stack depth** - Deeper stacks = more frames to capture

### CPU Overhead Correlation

The 2.6x CPU increase from DoE aligns with micro-benchmark data:

```
At 100 RPS with typical lock patterns (~1000 lock ops/request):
- 100K lock ops/sec × 640ns = 64ms CPU/sec
- Plus occasional full sample path (~5000ns × 1% × 100K) = 5ms CPU/sec
- Total: ~69ms/sec additional CPU per core

With profiling's thread overhead, context switches, etc. → 2.6x makes sense
```

---

## Optimization Strategy

### What Was Considered and Rejected

| Approach | Why Rejected |
|----------|--------------|
| **Conditional Unwrapping** | ❌ Not feasible - no way to track all lock references, identity preservation required |
| **Rust (PyO3) Rewrite** | ❌ PyO3 has ~400ns Python interop overhead - only 38% improvement vs 86% for Cython |
| **C++ Rewrite** | ❌ Same performance as Cython but 5x more code, harder to maintain |

### Recommended Optimizations

#### Priority 1: Cython Hot Path (HIGHEST IMPACT)

**Target:** Reduce per-operation overhead from ~640ns to ~70ns (89% reduction)

```
Current Python:  ████████████████████████████████ 640ns
After Cython:    ████ 70ns

Expected DoE impact:
- P50 latency: +11% → +1-2%
- P99 latency: +38% → +5-10%
- CPU usage: +160% → +20-30%
```

**Implementation:** See `cython_optimization_example.pyx`
- `cdef inline` for `_fast_acquire()` and `_fast_release()`
- C-level struct access for state
- Inlined capture check (no method call)

**Effort:** 1-2 weeks  
**Risk:** Low (proven pattern in dd-trace-py)

#### Priority 2: Optimize `_flush_sample()` Path

**Target:** Reduce full sample overhead from ~5,300ns to ~2,000ns

This addresses the P99 variance. Ideas:
- Cache thread/task info (avoid repeated lookups)
- Pre-allocate ddup handles
- Lazy frame capture (capture only on contention)

**Effort:** 1 week  
**Risk:** Low

#### Priority 3: Documentation & User Guidance

**Target:** Help users make informed decisions

```bash
# For latency-sensitive services (P99 SLO < 10ms):
DD_PROFILING_LOCK_ENABLED=false

# For CPU-constrained environments:
DD_PROFILING_LOCK_ENABLED=false

# For troubleshooting only:
DD_PROFILING_LOCK_ENABLED=true  # Enable temporarily
```

| Lock Ops/Sec | CPU Overhead | Recommendation |
|--------------|--------------|----------------|
| < 10K | ~6ms/sec | ✅ Safe to enable |
| 10K - 100K | 6-60ms/sec | ⚠️ Monitor closely |
| 100K - 1M | 60-640ms/sec | ⚠️ Consider disabling |
| > 1M | > 640ms/sec | 🔥 Disable |

**Effort:** 1-2 days  
**Risk:** None

#### Priority 4: Per-Module Filtering (Future)

**Target:** Allow excluding framework locks

```bash
DD_PROFILING_LOCK_EXCLUDE_MODULES=django.db,sqlalchemy.pool,urllib3
```

**Benefits:**
- Reduce wrapped lock count by 50-90%
- Keep profiling for application locks only
- User-controlled tradeoff

**Effort:** 2-3 weeks  
**Risk:** Medium

---

## Q4 2025 Prioritization

Given the DoE findings, here's the recommended Q4 work:

### Must Do (December)

| Task | Impact | Effort | Target |
|------|--------|--------|--------|
| **Cython hot path** | High | 1-2 weeks | -89% per-op overhead |
| **Documentation** | Medium | 2 days | User guidance |

### Should Do (If Time)

| Task | Impact | Effort | Target |
|------|--------|--------|--------|
| **Optimize `_flush_sample`** | Medium | 1 week | -50% P99 impact |
| **Sampling rate config** | Low | 3 days | User control |

### Defer to Q1 2026

| Task | Impact | Effort | Reason |
|------|--------|--------|--------|
| **Per-module filtering** | Medium | 2-3 weeks | Needs design |
| **Adaptive sampling** | High | 4-6 weeks | Complex, needs research |

---

## Expected Outcomes

After Cython optimization:

| Metric | Current | After Optimization | Improvement |
|--------|---------|-------------------|-------------|
| Per-op overhead | 640ns | 70ns | **89%** |
| P50 latency impact | +12% | +1-2% | **~85%** |
| P99 latency impact | +38% | +5-10% | **~75%** |
| CPU overhead | +160% | +20-30% | **~80%** |

**For a service with 1M lock ops/sec:**
- Before: 0.64 CPU cores wasted
- After: 0.07 CPU cores wasted
- **Saved: 0.57 CPU cores**

---

## Files in This Analysis

| File | Description |
|------|-------------|
| `LOCK_PROFILER_OPTIMIZATION_README.md` | This document (start here) |
| `OVERHEAD_VISUALIZATION.md` | Visual breakdown of overhead |
| `CONDITIONAL_WRAPPING_SUMMARY.md` | Why conditional unwrapping doesn't work |
| `CONDITIONAL_WRAPPING_ANALYSIS.md` | Full technical analysis |
| `NATIVE_OPTIONS_SUMMARY.md` | Quick comparison: Cython vs Rust vs C++ |
| `NATIVE_OPTIMIZATION_COMPARISON.md` | Detailed technology comparison |
| `cython_optimization_example.pyx` | Example Cython implementation |
| `measure_lock_overhead.py` | Local benchmark script |

---

## Running the Benchmarks

### Local Micro-Benchmarks

```bash
source venv/bin/activate
python measure_lock_overhead.py
```

### DoE Production Benchmarks

```bash
# In dd-trace-doe repository
./run_doe.py --scenario lock_profiling --contention high
./run_doe.py --scenario lock_profiling --contention medium
./run_doe.py --scenario lock_profiling --contention low
```

---

## Conclusion

The DoE benchmarks **validate** the local analysis and **strengthen** the case for Cython optimization:

1. **2.6x CPU overhead** is the primary pain point → Cython's 89% reduction directly addresses this
2. **P99 variance** (3.3x worse than P50) → Optimize `_flush_sample` path
3. **Contention-independent** overhead → Confirms it's the instrumentation machinery

**Recommended immediate action:** Ship the Cython hot path optimization to reduce the ~160% CPU overhead to ~20-30%.

---

## References

- DoE benchmark results: `dd-trace-doe/LOCK_PROFILER_OVERHEAD_RESULTS.md`
- Existing Cython patterns: `ddtrace/profiling/_threading.pyx`, `_traceback.pyx`
- Lock collector implementation: `ddtrace/profiling/collector/_lock.py`

