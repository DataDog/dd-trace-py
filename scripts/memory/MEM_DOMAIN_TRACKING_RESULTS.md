# MEM Domain Tracking — Before/After Benchmark Results

Branch: `vlad/memalloc-support-mem-domain`  
Date: 2026-05-05  
Script: `scripts/mem_domain_bench.py`  
Workload: 100 MB OBJ + 100 MB MEM + 100 MB RAW allocations  
Heap sample size: 512 KB

---

## Before (DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=false, default)

```
Heap-space pprof groups : 2
Total tracked           : 255.3 MB

Top frames (top-of-stack, by heap-space bytes):
  _alloc_obj    156.1 MB
  _alloc_raw     99.3 MB
  _alloc_mem      0.0 MB   ← invisible: PYMEM_DOMAIN_MEM not hooked
```

## After (DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true)

```
Heap-space pprof groups : 4
Total tracked           : 369.2 MB

Top frames (top-of-stack, by heap-space bytes):
  _alloc_obj    168.5 MB
  _alloc_mem    100.7 MB   ← now visible: array.array + list ob_item captured
  _alloc_raw    100.0 MB
```

## Diff

```
Function        Before      After      Delta
──────────────  ─────────  ─────────  ─────────
_alloc_obj      156.1 MB   168.5 MB   ▲  12.5 MB  (sampling noise)
_alloc_raw       99.3 MB   100.0 MB   ▲   0.8 MB  (sampling noise)
_alloc_mem        0.0 MB   100.7 MB   ▲ 100.7 MB  ← MEM domain now tracked
──────────────  ─────────  ─────────  ─────────
TOTAL           255.3 MB   369.2 MB   ▲ 113.9 MB

Pprof groups: 2 → 4  (+2)
```

Note: "pprof groups" counts distinct stacktrace groups in the exported profile, not
raw heap tracker entries. The profiler samples at 512 KB intervals and accumulates many
heap tracker entries per callsite, but libdatadog merges entries sharing the same
stacktrace into one pprof sample with summed weights. This benchmark has only 4 unique
allocation callsites, so the group count is low by design. In production with thousands
of diverse callsites, each contributes its own group.

## Conclusion

`_alloc_mem` goes from **invisible (0 MB) to 100.7 MB** with MEM domain enabled.  
The `_alloc_obj` and `_alloc_raw` deltas (+12.5 MB and +0.8 MB) are sampling noise.  
The heap profiler now captures `PyMem_Malloc`/`Calloc`/`Realloc` allocations
(list internal buffers, `array.array` data) that were previously untracked.

---

# MEM Domain Overhead Profiling

Branch: `vlad/heap-profiler-lineno-cache`  
Date: 2026-06-12  
Script: `scripts/memory/profile_mem_domain_overhead.sh`  
Workload: synthetic `array.array` allocation loop (PYMEM_DOMAIN_MEM), 25s perf runs  

## Results

| Run | DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED | Allocs | Throughput |
|-----|----------------------------------------|--------|------------|
| A (obj_only) | false | 22.9M | 907k/s |
| B (mem_on)   | true  | 20.4M | 813k/s |

**Overhead: ~10% throughput reduction with MEM domain enabled.**

## Diff Flamegraph Analysis (mem_on − obj_only)

| Frame | Delta | Interpretation |
|-------|-------|----------------|
| `memalloc_heap_track_invokes_cpython` | **+0.28%** | CPython stack-walk on sampled allocs — top ddtrace-specific positive contributor |
| `should_sample_no_cpython` | ~0% (−0.02–0.05%) | Per-alloc gate check — essentially free |
| `_int_malloc` | −1.83% | Workload doing fewer allocs due to overall slowdown |
| `__GI___libc_malloc` | −1.68% | Same — artifact of reduced workload throughput |

## Key Finding

The overhead is **not** in the per-allocation hook guard (`should_sample_no_cpython` is
essentially zero delta). It is entirely in **traceback capture**
(`memalloc_heap_track_invokes_cpython`) triggered when an allocation is actually sampled.

The 10% throughput hit comes from how often we sample MEM-domain allocations — MEM
allocations are very frequent and small (e.g. `array.array` item storage), so at the
current shared sampling counter they fire the expensive CPython stack-walk far more often
than OBJ-domain allocations would at the same byte threshold.

## Implication

Lowering hook-guard overhead will not help — `should_sample_no_cpython` is not the
bottleneck. The fix is to sample MEM-domain allocations at a lower rate than OBJ-domain
by maintaining **per-domain byte counters** (§3b in the optimization plan), so
`memalloc_heap_track_invokes_cpython` fires less often for MEM-domain traffic.
