# Investigation: `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true` reported multi-TiB heap-live-size ("phantom leak")

> **Status (reassessed):** the two tracker-side root causes originally proposed
> (estimator blow-up; ghost accumulation) were **built and tested locally and
> neither reproduces** — the heap tracker reports `heap-space` accurately and does
> not leak live entries. The leading explanation is now a **comparison-view
> aggregation + newly-visible MEM-domain heap** artifact (not a tracker defect);
> a residual workload-specific native-free-bypass is not excluded. Confirming
> either needs **per-profile ddstaging data**, currently blocked on org auth. See
> “Root cause status (REASSESSED)”.

## Summary
When the memory profiler's MEM-domain hooks are enabled
(`DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true`, Python ≥ 3.12), the reported
**heap-live-size** metric diverges from real process memory by ~5 orders of
magnitude. On a real staging workload (`ai_gateway`, staging) the profiler
reported **1.82–4.2 TiB** of live Python heap and a steadily *growing* curve,
while the container's real **RSS stayed flat** in the single-digit-GiB band
(6624 MiB limit, no OOMKills). The bogus series is strong enough to trip
Datadog's automated **APM Memory Leaks** flow into auto-answering
"Python Live Heap grows over time → Yes," i.e. **the feature manufactures a
phantom memory leak.**

- **Severity:** High for the feature (GA-blocker). Low immediate prod risk
  (real RSS unaffected; metric-only corruption).
- **Affected:** `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true`, CPython ≥ 3.12.
  Off by default, so default installs are unaffected.
- **Status/workaround:** Keep the knob **off** (default). The staging
  integration rollout that surfaced this has been reverted
  (`dd-source` `ai-gateway/staging`, revert of `0700383b7eb48`).

## Evidence
Real staging `ai_gateway`, before (24 h baseline) vs MEM-domain-ON window (~4.5 h):

| Signal | Baseline (A) | MEM-domain ON (B) | Note |
|---|---|---|---|
| Heap live-size | 56 MiB | **1.82 TiB (+3,439,086%)** | physically impossible vs limit |
| Heap live-size (later 2 h vs earlier 2 h) | 2.91 TiB | 4.2 TiB (+44%) | still *climbing* |
| Allocated bytes/min | 218 MiB | 166 MiB | load noise (uncontrolled) |
| CPU-time/min | 5.02 s | 3.52 s | load noise (uncontrolled) |
| Container **RSS** | flat | **flat** | decisive: no real leak |

Top "grew in B" frames: `stdlib` (1.41 TiB), `load_tiktoken_bpe`,
`_normalize_header_key` (`_models.py`), `HttpToolsProtocol.on_header/connection_made`,
`enforce_headers`, `JSONDecoder.raw_decode`, `Request.stream`, pydantic
`FieldInfo.*`/`ModelMetaclass` — i.e. lots of **small, high-churn** buffers.

The contradiction (TiB "live" heap on a ~5 GiB-RSS pod) is the finding: the
metric is decoupled from reality.

## Where it lives
- `ddtrace/profiling/collector/_memalloc.cpp`
  - MEM-domain hooks installed on `start(..., mem_domain_enabled=true)`
    (`PyMem_SetAllocator(PYMEM_DOMAIN_MEM, ...)`), symmetric malloc/calloc/realloc/free.
  - `memalloc_free_mem` **does** call `memalloc_heap_untrack_no_cpython(ptr)` —
    so this is *not* a plain missing-free-hook bug.
  - Free hot-path early-exits when the saved allocator is null/no-free
    (`if (!saved) return;` / `if (!alloc.free) return;`) — untrack is skipped there.
- `ddtrace/profiling/collector/_memalloc_heap.cpp`
  - Single pointer→traceback map `allocs_m` shared across domains; **domain is
    ignored** on track: `memalloc_heap_track_invokes_cpython(...)` → `(void)domain;`.
  - Map is **hard-capped**: `should_sample_no_cpython` bails at
    `allocs_m.size() >= TRACEBACK_ARRAY_MAX_COUNT` (== `UINT16_MAX` == 65535,
    `++cap_drops`) → bounds real memory (consistent with flat RSS).
  - Weighting on each sampled alloc:
    ```
    double s = size > 0 ? size : 1;
    double r = get_sample_size();
    double p = 1.0 - exp(-s / r);
    int64_t heap_count = 1.0 / p;            // ≈ r/s for small s
    tb->sample.push_heap(allocated_memory_val, heap_count);  // (bytes≈R, count≈r/s)
    ```
- `ddtrace/internal/datadog/profiling/dd_wrapper/src/sample.cpp` — `push_heap(size, count)`:
    ```
    values[heap_space] += size;   // DDOG_PROF_SAMPLE_TYPE_HEAP_SPACE  (the TiB metric)
    values[heap_count] += count;  // DDOG_PROF_SAMPLE_TYPE_HEAP_LIVE_SAMPLES
    ```
  `size` and `count` are **independent pprof value types** — they are *not*
  multiplied. This is the decisive fact for root-causing (below).

## Root cause status (REASSESSED — both original hypotheses RULED OUT locally)
**Update (local build + reproduction on py3.13, real compiled extension):** the
two candidate root causes were tested directly and **neither reproduces.** The
earlier "CONFIRMED: ghost accumulation" conclusion is **withdrawn.**

- **(A) Estimator inflates `heap-space` — RULED OUT (source + measurement).**
  `heap-space` is `values[heap_space] += size` with `size == allocated_memory_val
  ≈ R`; the Horvitz–Thompson weight `1/p` is a *separate* pprof value type
  (`heap-live-samples`) and the two are never multiplied. Measured end-to-end,
  reported `heap-space` tracks real live bytes to within ~2× even adversarially
  (see “Local reproduction evidence”). No blow-up.
- **(B) Ghost accumulation (freed-but-not-untracked entries) — NOT REPRODUCIBLE.**
  With per-domain track/untrack instrumentation compiled in, every workload tried
  keeps `track_obj + track_mem == untrack` **exactly**, `live → 0` after
  `gc.collect()`, and never approaches the 65535 cap (`evictions = 0`): bytearray
  churn, bytearray grow-realloc, list-append realloc, 20k held-then-dropped,
  4-thread concurrent churn, and GC-cycle-only frees. The MEM free hook untracks
  correctly in all of them.

**Therefore the deployed heap tracker reports `heap-space` accurately in every
workload we can construct, and does not leak live entries.**

- **Secondary "counter-inflation" idea investigated and ruled out too:** when
  `should_sample` returns true but the sample is then aborted (reentrancy guard,
  or `instance == nullptr`), `allocated_memory` is not reset, which could inflate
  the *next* sample's weight. Confirmed harmless: the aggregate `heap-space ≈ real
  bytes` (ratio 1.00–2.03) shows the weight is well-behaved in steady state.

### Leading hypothesis now: comparison-view aggregation + newly-visible MEM heap
This report's own note (per-profile `heap-space` is bounded by `65535 × R`, and
the TiB figure is that value **summed across ~4.5 h of uploads × ~10 pods**) plus
the local measurements point at a **visualization/measurement artifact, not a
tracker defect**:
- Uploads land ~every 60 s ⇒ ~270 profiles/pod × ~10 pods ≈ **2,700 profiles** in
  the 4.5 h window. If each pod legitimately holds ~0.6–1 GiB of MEM-domain live
  heap that was **invisible before** (tiktoken BPE tables, HTTP header buffers,
  pydantic models — all `PyMem_*` allocations the OBJ-only hook never saw), a
  summed comparison view yields ~1.8 TiB. That matches the observed 1.82 TiB.
- The A/B is apples-to-oranges: baseline **A had `mem_domain` OFF**, so it could
  not see any MEM-domain live heap at all; enabling it makes real, previously-
  hidden memory appear as enormous "growth."
- "Grows over time" + "flat RSS" is consistent with a cumulative/summed series
  (grows mechanically with #profiles) over a real, bounded working set — not a leak.

### Residual possibility (not excluded — needs raw staging data)
A **workload-specific** untrack bypass we didn't reproduce — e.g. a C extension in
`ai_gateway` that frees `PyMem_*` memory via `PyMem_RawFree`/libc `free` (unhooked
RAW domain) — would still cause real ghosts. Local repro covers common Python
patterns, not every native free path. Settling this requires **per-profile**
staging data (single-profile `heap-space` value + live sample count + whether the
map is pinned at 65535), which is currently **blocked**: the profiler data lives on
`ddstaging.datadoghq.com` and available creds are prod-org only
(`check_staging_profiling_auth` → `ok:false`, 401/403).

## Local reproduction evidence (py3.13, real compiled extension)
Built via `riot` with a git override so `FetchContent` clones googletest over
public https (see “Build/verification”); measured with `_memalloc.start(...,
mem_domain_enabled=True)` and the pprof pipeline.

**Track/untrack balance (ghost hypothesis) — `[memalloc-heap]` stderr counters:**

| Workload | track_obj+track_mem | untrack | live after gc | evictions |
|---|---|---|---|---|
| 2000× bytearray churn | 1977 | 1977 | 0 | 0 |
| bytearray grow-realloc | 5901 | 5901 | 0 | 0 |
| list-append realloc | 8653 | 8653 | 0 | 0 |
| 20k held → dropped | 8844 | 8844 | 0 (177 while held) | 0 |
| 4-thread concurrent | 26186 | 26186 | 0 | 0 |
| GC-cycle-only frees | 26974 | 26974 | 0 | 0 |

Balanced everywhere; cap never approached ⇒ **no ghosts.**

**Reported `heap-space` bytes vs real live bytes (estimator hypothesis):**

| Workload | real | reported heap-space | ratio |
|---|---|---|---|
| 200× 1MB bytearray | 209.7 MB | 208.7 MB | **1.00** |
| 4000× 64KB bytearray | 262.1 MB | 262.6 MB | **1.00** |
| 300k tiny lists (R=1MB) | 19.2 MB | 31.9 MB | 1.66 |
| 500k× 64B (R=8KB) | 32.0 MB | 65.1 MB | 2.03 |

`heap-space` (bytes) tracks reality within ~2× even adversarially. The only large
dimension is `heap-live-samples` (object *count*, e.g. ~564k for 300k live
objects) — a roughly-correct count estimate, **not** bytes, and not the "TiB"
metric.

## Reproduction / regression guard
- **Automated:**
  `tests/profiling/collector/test_memalloc.py::test_mem_domain_churn_does_not_inflate_live_heap`
  churns MEM-domain allocations (allocate+free in a loop, `gc.collect()`), then
  asserts the live sample count attributed to the churn stays `< 50` and does not
  scale with iteration count. **Note:** this test **passes on HEAD as well as with
  the branch changes** — because there is no reproducible ghost bug for this
  workload (`evictions=0`, so the self-healing cap never even fires). It is a
  useful **regression guard**, not a bug-demonstrating (pre-fix-failing) test. Its
  docstring, which claims "fails pre-fix," is inaccurate and should be corrected.
- **Not yet reproduced:** a workload that actually makes `track ≠ untrack`. To
  chase the residual native-free-bypass possibility, a soak mimicking the staging
  hot frames (httptools header parse + `json.loads`, pydantic) with the
  `_DD_MEMALLOC_HEAP_DEBUG_STATS=1` counters on would be the next step — but the
  clean patterns above already exonerate ordinary Python alloc/free.

## What to do now (given the reassessment)
Because the tracker demonstrably reports `heap-space` accurately and does not leak
live entries, **there is no proven tracker bug to “fix.”** Recommended order:

1. **Do NOT ship a fix framed as closing a ghost leak.** The eviction change on
   this branch (below) is a *robustness net*, not a root-cause fix, and it has a
   real downside — evicting the oldest **still-live** entries makes the profiler
   blind to genuinely long-lived allocations (i.e. real leaks). Don’t enable it
   as “the fix.”
2. **Get per-profile staging data** (blocked on ddstaging org auth) to decide
   between the two live hypotheses: (a) comparison-view aggregation + newly-visible
   MEM heap (most likely — needs no code change, only a docs/measurement fix), vs
   (b) a workload-specific native untrack bypass (would need a targeted fix).
   Ask #profiling-backend for a staging cookie or an app key with ddstaging
   profiling access, then read a single profile’s `heap-space` value, live sample
   count, and `heap_tracker_size`/`cap_drops`.
3. **If (a):** the fix is to the A/B methodology and/or UI aggregation, not the
   tracker; keep `mem_domain` off by default until the comparison is apples-to-
   apples (same wheel, mem-domain on vs off, identical replayed corpus).
4. **Keep only the diagnostic** (per-domain counters + `cap_drops`) as a small,
   always-safe observability aid; consider it independently of the eviction.

## Change currently on this branch (built + tested; NOT a root-cause fix)
`ddtrace/profiling/collector/_memalloc_heap.cpp` — now **compiled and run
end-to-end** on py3.13 (all mem-domain tests pass, all 4 `PYTHONMALLOC` variants):
1. **Self-healing cap.** `should_sample_no_cpython` previously *froze* the sampler
   once `allocs_m` hit `TRACEBACK_ARRAY_MAX_COUNT` (65535); it now evicts the
   **oldest** live entry (`evict_oldest_no_cpython`, insertion-order
   `std::deque<void*>` with lazy tombstone draining — std-allocated, no hook
   re-entry). **In every workload measured this never triggers (`evictions=0`),**
   so it is purely defensive. Downside noted above (evicts real long-lived
   allocations at saturation).
2. **Track/untrack instrumentation** — per-domain track counters + untrack
   counter, logged from `export_heap_no_cpython` when `_DD_MEMALLOC_HEAP_DEBUG_STATS`
   is set; eviction count surfaced via the existing `heap_tracker_cap_drops`
   ProfilerStat. This is what produced the “ruled out” evidence above and is worth
   keeping.

## Build / verification (works locally now)
The earlier “sandbox has no network” blocker is resolved on the dev machine. The
only obstacle was a global git rewrite (`url.git@github.com:.insteadof
https://github.com/`) that forced `FetchContent`’s googletest clone onto SSH
(auth-fails). Scope a per-invocation override so only `google/*` stays on public
https (longest-prefix wins; the rest of your SSH setup is untouched):

```bash
GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0="url.https://github.com/google/.insteadOf" \
GIT_CONFIG_VALUE_0="https://github.com/google/" \
DD_PROFILING_NATIVE_TESTS=0 \
riot run -p 3.13 profile-memalloc -- -k test_mem_domain

# Diagnostic counters on stderr (direct, avoids pytest capture):
_DD_MEMALLOC_HEAP_DEBUG_STATS=1 .riot/venv_py31312/bin/python -c '...start/churn/heap()...'
# → [memalloc-heap] live=.. track_obj=.. track_mem=.. untrack=.. evictions=..
```
Still owed before any hook change lands per native-code rules §9: the
data-race/crash-stress memalloc suite (`test_memalloc_allocator_hook_does_not_
release_gil`, fork tests) must stay green.

## Real cost of the feature — not estimable from this run
The ambient integration data **cannot** cost the feature: window A is a
different wheel (not "mem_domain off, same build"), load is uncontrolled
(hence the meaningless −31% CPU), and the memory metric is corrupted. Bounds we
*can* state: real memory overhead is **bounded** (map cap) and **below RSS
noise** (no real leak); attributable CPU is the profiler's own frames
(`untrack_no_cpython` ~15 ms/min + `memalloc_*` ~2–4 ms each ≈ <0.1% core in
this window) and **grows with map occupancy**. A clean number requires the
**controlled Test-Drive A/B** (identical replayed corpus, same wheel,
`mem_domain` on vs off) *after* the estimator/untrack fix.

## Notes
- Real RSS flat → **no OOM risk** from this; it is a **metric-correctness**
  defect, not a memory leak.
- But it is a **GA-blocker**: `heap-live-size` is a first-class metric feeding
  the Profiling UI, Watchdog/auto-insights, and the Memory Leaks flow. Shipping
  `mem_domain` on-by-default would make heap profiling unusable for any Python
  service and generate false leak signals fleet-wide.
