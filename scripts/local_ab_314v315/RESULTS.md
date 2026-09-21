# Local 3.14 vs 3.15 profiling smoke A/B — verified results

Single-run laptop soak. **Not** staging load, **not** multi-repeat, **not** a claim about production RSS.

## How to re-run

From a checkout of this branch (or any tip that contains `scripts/local_ab_314v315/`):

```bash
./scripts/local_ab_314v315/run.sh
DURATION=90 DDTRACE_SRC=$PWD ./scripts/local_ab_314v315/run.sh
REUSE_VENV=/tmp/local314v315_.../venvs DURATION=90 ./scripts/local_ab_314v315/run.sh
```

Requires `PY314_BIN` (default Homebrew `python3.14`) and `PY315_BIN` (default pyenv `3.15.0a7`).

## Run identity (verified from artifacts)

| Field | Value |
| --- | --- |
| `RUN_DIR` | `/tmp/local314v315_ddtracepy_20260921T033620Z` (still present when this note was written; small copy under `runs/20260921T033620Z/`) |
| Harness tip at run time | `822dd5a3fa158883b368965f081c97ccaa32c617` (`summary.json` / `delta_table.json`) |
| Tip parent | `faae7e3b2b` (PR #19272 merge base claimed in chat — matches `822dd5a^`) |
| Branch tip when this note was committed | see `git log -1` on `vlad/chore-local-ab-314v315` (harness may have been rebased since the run) |
| Editable install | `/private/tmp/dd-trace-py-19272-local314v315` → `ddtrace==4.16.0rc1` |
| A Python | `/opt/homebrew/bin/python3.14` → **3.14.6** (server log) |
| B Python | `~/.pyenv/versions/3.15.0a7/bin/python` → **3.15.0a7** (server log) |
| Duration | **90 s** drive window (`DURATION=90`) |
| Concurrency | **2 workers / side** |
| Ports | A `18400`, B `18401` |
| Profiling | `DD_PROFILING_ENABLED=true`, lock+memory on, upload interval 15 s, local `DD_PROFILING_OUTPUT_PPROF` |
| Tracing | `DD_TRACE_ENABLED=false` |
| Repeats | **1** (single soak) |

In-repo copy of small artifacts: `scripts/local_ab_314v315/runs/20260921T033620Z/` (`delta_table.json`, `summary.json`, `proc_metrics.csv`, `drive_stats.json`, `drive.log`, `metadata/*/profile.*.internal_metadata.json`). **No** `.pprof` binaries committed.

Upstream prototype this harness ports:  
`experimental/teams/profiling-python/ddtrace-upgrade/smoke_ab/local_314v315/`  
(boot + pprof presence only — **no** `ps` RSS/CPU series, **no** delta table, **no** metadata sums).  
Nothing matching under `prof-correctness`.

---

## Did you use “the metrics”? (metric sources)

**Yes for process RSS/CPU and HTTP drive counts; partially for profiler metadata; no for pprof sample-type totals / memray / latency.**

| Source | Used by harness for published table? | Notes |
| --- | --- | --- |
| `ps` 1 Hz `%cpu` + `rss` on server PIDs (`logs/proc_metrics.csv`) | **Yes** | macOS `ps`: `%cpu` = decaying average (can exceed 100%); `rss` = resident set in KiB. Window ≈ t=0…91 s during drive; **n=89** samples/side (missing t=17,55,90). |
| Drive counters (`drive_stats.json` / `drive.log`) | **Yes** | GET total / ok / err only. **No** latency percentiles. |
| Server access log path counts | **Yes** (asyncio_burst hits only) | Regex `asyncio_burst` in `server_*.log`. |
| pprof `*.internal_metadata.json` | **Yes** (`sample_count`, `asyncio_task_count`) | Summed all metas present at summary time (**7** files/side). |
| Field `sample_capture_cpu_us` | **Looked up, always 0** | **Wrong key.** Real field is `sample_capture_cpu_time_us` (see `profiler_stats.cpp` on this tip). Pre-fix published sum = 0 is a **harness bug**, not zero profiler CPU. Fixed in `run.sh` after this run; published zeros below are from the **pre-fix** artifacts. |
| pprof sample types (`cpu-time`, `alloc-space`, `heap-space`, domains `mem`/`obj`) | **Not** in harness table | Present on disk; decoded **post-hoc for this note only** (`zstd -d` + `go tool pprof -tags -sample_index=heap-space`). A future harness revision should fold `heap-space` by domain into the delta table. |
| Memray | **No** | Not installed / not run. |
| Agent / Datadog metrics API | **No** | Local pprof files only. |
| GC / allocator RSS from CPython | **No** | Only process RSS. |

Other reading of “did you use the m…” that fits the harness comments: **methodology** of the experimental `smoke_ab/local_314v315` (same corpus + drive shape + profiling on). This dd-trace-py harness **extends** that methodology with `ps` sampling and a delta table; it does **not** stop at “boots and wrote pprof.”

---

## Accuracy verdict

Headline chat numbers for req, req/s, errors, CPU%, RSS, and `sample_count` **recompute exactly** from `drive_stats.json` + `proc_metrics.csv` + the **7** metas that existed when `summary.json` was written.

Misleading / wrong relative to the raw tree:

1. **`sample_capture_cpu_us` = 0** — harness bug (wrong JSON key). Correct first-7 sums: A **882333 µs** (~0.88 s), B **943357 µs** (~0.94 s), Δ **+6.9%**.
2. **`pprof .pprof files` = 7** — true at summary time; the tree now has **8** per side (post-drive flush / kill). Sample sums in the table match the **first 7**, not all 8.
3. **CPU%** is `ps` decaying average of the **whole server process** (app + profiler threads), not app-only and not profiler-only.
4. **RSS** is process RSS of that same PID, including profiler live heap / sample buffers — not “application objects only.”
5. **Warmup included**: means include t≈0…15 s ramp; dropping first 15 s still leaves ~+14.7% RSS mean (so not a startup-only artifact).
6. **Single 90 s laptop soak** — do not treat ±1–2% req/s or CPU mean as a regression signal.

Harness post-fix: `run.sh` now reads `sample_capture_cpu_time_us`. **Do not re-interpret the saved `delta_table.json` as if it had been re-run.**

---

## Verified metric table (from raw artifacts)

Epistemic labels: **verified** = recomputed from named file; **inferred** = arithmetic on verified; **unknown** = not measured.

Window: one 90 s concurrent drive; CPU/RSS from 89 `ps` rows/side over ~91 s wall; profiles every 15 s.

| Metric | A 3.14.6 | B 3.15.0a7 | Δ | Δ% | Status |
| --- | --- | --- | --- | --- | --- |
| req total (90 s) | 449 | 456 | +7 | +1.6% | verified `drive_stats.json` |
| req/s | 4.99 | 5.07 | +0.08 | +1.6% | inferred total/90 |
| errors | 0 | 0 | 0 | 0% | verified |
| error rate | 0% | 0% | 0 | 0% | verified (`codes` empty) |
| HTTP 200 in server log | 451 | 458 | — | — | verified (drive + healthz + post-drive asyncio probe) |
| CPU% mean (`ps` 1 Hz) | 50.28 → **50.3%** | 50.11 → **50.1%** | −0.2 pp | −0.3% | verified `proc_metrics.csv` |
| CPU% p95 | 81.2% | 92.2% | +11.0 pp | +13.5% | verified (nearest-rank index) |
| CPU% max | 103.8% | 100.8% | −3.0 pp | −2.9% | verified |
| RSS mean | 90.95 → **90.9 MiB** | 104.54 → **104.5 MiB** | +13.6 MiB | +14.9% | verified (`rss_kb`/1024) |
| RSS p95 | 103.6 MiB | 119.7 MiB | +16.1 MiB | +15.6% | verified |
| RSS max | 107.3 MiB | 121.2 MiB | +13.8 MiB | +12.9% | verified |
| asyncio_burst log hits | 50 | 51 | +1 | +2.0% | verified server log |
| asyncio_task_count mean (meta) | 3.00 | 3.00 | 0 | 0% | verified (7 metas @3) |
| `.pprof` files at summary | 7 | 7 | 0 | 0% | verified |
| pprof bytes (all artifacts @summary) | 50.8 MB | 42.8 MB | −8.0 MB | −15.7% | verified (matches idx≤7 tree) |
| `sample_count` sum (7 metas) | 107456 | 118449 | +10993 | +10.2% | verified metadata |
| published `sample_capture_cpu_us` | **0** | **0** | 0 | 0% | verified as published; **wrong field** |
| true `sample_capture_cpu_time_us` (7 metas) | 882333 | 943357 | +61024 | +6.9% | verified metadata (post-hoc) |
| `sampling_event_count` (7 metas) | 27019 | 29177 | +2158 | +8.0% | verified (not in chat summary) |
| `heap_tracker_cap_drops` | 0 | 0 | 0 | — | verified |
| copy_memory_error_count (7) | 2 | 0 | −2 | — | verified |
| latency p50/p95 | — | — | — | — | **unknown** (not recorded) |
| memray / RSS breakdown by allocator | — | — | — | — | **unknown** (not run) |

`delta_table.json` / `summary.json` match the verified rows above (including the bogus CPU-us zeros).

---

## RSS attribution (+~15% mean)

**Claim:** process RSS mean 90.9 → 104.5 MiB (+13.6 MiB, +14.9%).

| Hypothesis | Verdict | Artifact |
| --- | --- | --- |
| Measurement / warmup-only artifact | **Unlikely** | `proc_metrics.csv`: Δ appears by t≈3 s and stays; mean after dropping first 15 s still **+14.7%**; after 30 s **+15.9%**. Median paired Δ ≈ **13.5 MiB**. |
| More HTTP load on B | **Does not explain** | Only +1.6% requests; both sides same corpus / seed / concurrency. |
| Profiler sample volume alone | **Insufficient / unsupported as sole cause** | Meta `sample_count` +10.2% and capture CPU +6.9%, but **pprof on-disk bytes are smaller on B** (−15.7%). No harness metric maps sample_count → RSS bytes. |
| Live heap tracked by profiler (`heap-space`) | **Partial — bounds the cause at ~¼** | `go tool pprof -tags -sample_index=heap-space` on all 7 profiles/side. Mean tracked live heap A **8.56 MB** (range 7.63–10.64), B **11.77 MB** (range 7.48–19.80), Δ **+3.21 MB (+37.5%)**. That is **~23% of the 14.25 MB process-RSS gap** — the other ~77% is **not** visible in these profiles. |
| …split by allocator domain | **`mem` domain carries it** | Same command. Domain means: `mem` A **3.02** → B **6.84 MB** (Δ **+3.82 MB**, ~27% of the RSS gap); `obj` A **5.55** → B **4.93 MB** (Δ **−0.62 MB**). So the tracked increase is raw-`PyMem` domain, not object domain. Caveat: A’s `obj` is pinned at exactly **5.55 MB** in all 7 profiles (**suspicious** — possible sampler artifact, **unverified**). |
| `alloc-space` cumulative | **Not usable for RSS** | Totals are TB-scale sampled allocation volume per 15 s window (workload churn via `alloc_pressure` / `pure_mem`), not resident set. |
| 3.15 allocator / pymalloc behavior | **Plausible, unproven** | Profiles show `mem` and `obj` domains on both sides with `memory.mem_domain_enabled=true`, but **no** controlled allocator A/B and **no** memray. Cannot attribute X MiB of the +14.9% to pymalloc vs interpreter vs profiler. |
| Profiler overhead vs app | **Unknown split** | Single PID RSS; no profiler-off control run. |

**Bottom line:** The +~15% RSS is a **real process-RSS difference** on this soak (**verified** `proc_metrics.csv`).

- **Verified:** ~**23%** of the gap (**+3.21 MB** of **14.25 MB**) shows up as profiler-tracked live heap, and that increase sits in the **`mem` (raw `PyMem`) domain**, not `obj`.
- **Unknown:** the remaining **~77%**. No artifact in this run accounts for it — candidates (interpreter/arena behavior, profiler buffers, fragmentation) are **untested** here.
- Do **not** cite “3.15 allocator” or “profiler sample volume” as *the* cause. The `mem`-domain signal is a **lead worth a controlled follow-up** (profiler-off control + memray), not a conclusion.

---

## Full pprof sample-type dump (post-hoc, verified)

Decoded live `/tmp/.../pprof/{A314,B315}/*.pprof` (`zstd -d` → `go tool pprof -raw`). Totals = sum of raw sample values over profiles **1–7** (exclude tiny shutdown `.8`). heap-space is a **gauge** — report mean of the 7 windows, not the sum.

| Sample type | A 3.14.6 | B 3.15.0a7 | Δ | Δ% | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| cpu-time | 88.57 s | 86.25 s | −2.32 s | −2.6% | verified |
| cpu-samples | 107456 | 118449 | +10993 | +10.2% | verified (= meta) |
| wall-time | 429.7 s | 428.2 s | −1.5 s | −0.4% | verified |
| wall-samples | 108824 | 120413 | +11589 | +10.7% | verified |
| exception-samples | 1143 | 1118 | −25 | −2.2% | verified |
| lock-acquire-wait | **0** | **0** | 0 | — | schema present, empty |
| lock-acquire | **0** | **0** | 0 | — | schema present, empty |
| lock-release-hold | **0** | **0** | 0 | — | schema present, empty |
| lock-release | **0** | **0** | 0 | — | schema present, empty |
| alloc-space | 14.49 TB | 12.09 TB | −2.39 TB | **−16.5%** | verified (churn, not RSS) |
| alloc-samples | 631.9M | 534.3M | −97.7M | −15.5% | verified |
| heap-space **mean** | 8.56 MiB | 11.77 MiB | +3.21 MiB | +37.5% | verified gauge |
| heap-live-samples **mean** | 55.0k | 96.3k | +41.3k | +75% | verified |
| gpu-time / gpu-samples / gpu-space / gpu-alloc-samples / gpu-flops / gpu-flops-samples | 0 | 0 | 0 | — | schema only |
| asyncio *(as sample type)* | **absent** | **absent** | — | — | only `asyncio_task_count` in metadata |

Per-window heap-space (MiB) A: 10.64, 8.76, 8.63, 8.02, 8.63, 7.63, 7.63 · B: 9.31, **19.80**, 11.91, 9.10, 7.48, 13.79, 11.03.

Domain means (same 7 windows, `pprof -tags`): `mem` 3.02→6.84 MiB (+3.82); `obj` 5.55→4.93 (−0.62). A’s `obj` pinned at 5.55 across all 7 (**suspicious**, possible sampler artifact — **unverified** cause).

### Locks (one finding)

Lock collector **enabled** (`lock.enabled=true` in `*.info.json`). All four lock sample types exist in every profile’s sample-type list and sum to **exactly 0** on both sides across all 8 windows (**verified** `-raw`). There is **no** lock wait time, acquire count, or lock-name attribution in this run — B is not “worse on locks”; locks were not observed. **Guess:** smoke app + default `lock.exclude_modules` (asyncio/threading/http/…) yields no retained lock samples. A lock-contended microbench would be needed to compare lock behavior.

### Capture / metadata (fuller)

| Field | A (7 / all-8) | B (7 / all-8) | Notes |
| --- | --- | --- | --- |
| `sample_capture_cpu_time_us` | 882333 / 932383 | 943357 / 994103 | verified; published table used wrong key → 0 |
| `sampling_event_count` | 27019 / 29109 | 29177 / 31336 | verified |
| `heap_tracker_count` mean | ~3.7 | ~7 | verified; B higher tracker occupancy |
| `heap_tracker_cap_drops` | 0 | 0 | verified |
| `copy_memory_error_count` | 2 / 2 | 0 / 0 | verified |
| `greenlet_count` | 0 | 0 | verified |

### Process series shape (not just mean)

`proc_metrics.csv` n=89/side, t=0…91 s (**verified**):

| Window | A RSS mean | B RSS mean | A CPU mean | B CPU mean |
| --- | ---: | ---: | ---: | ---: |
| early t≤15 | 73.0 MiB | 84.8 MiB | 52.5% | 48.3% |
| mid 30–60 | 93.1 MiB | 114.0 MiB | 46.1% | 55.1% |
| late t≥60 | 97.3 MiB | 107.2 MiB | 54.7% | 49.5% |

Paired RSS Δ (B−A) at t=0/5/15/30/45/60/75/89: **+0.6 / +11.6 / +14.1 / +16.7 / +23.0 / +22.3 / +8.1 / +18.7 MiB**. Gap appears by ~t=5 and persists — **not** a late-soak leak only.

RSS vs heap-space: shapes **diverge** (B heap spikes at profile 2 then varies; RSS stays elevated). **Inferred:** process RSS does **not** track profiler `heap-space` 1:1.

### Top frames (mid window profile `.3`, verified `-top`)

- **cpu-time A:** `ctypes.PyMem_Malloc` 23%, `math.factorial` 19%, `handle_alloc_pressure` 18%, `ddup.upload` 6%.
- **cpu-time B:** `handle_alloc_pressure` 20%, `PyMem_Malloc` 20%, `builtins.exec` 18%, `math.integer.factorial` 16%, upload 6%.
- **alloc-space both:** `handle_pure_mem` ≈98% of TB-scale sampled churn (workload by design).
- **heap-space A:** `handle_dynamic_code` 59%, `format_datetime` 30%. **B:** `handle_dynamic_code` ~77% cum + dynamic `_f_*` frames.
- **exception-samples both:** 100% `handle_factorial`.
- **lock-acquire both:** empty top (0 samples).

### Experimental twin

`experimental/.../smoke_ab/local_314v315/` (when present upstream): boot + pprof presence only — **no** `ps` series, **no** sample-type table. This harness already exceeds that. Nothing matching under `prof-correctness` (**verified** listing at analysis time; twin path absent from this worktree / main checkout).

---

## Advertising / memalloc verdict

**Do we need to prioritize memalloc optimizations before advertising 3.15 profiling support?**

**No — that premise is wrong given these profiles.**

| Question | Answer | Epistemic |
| --- | --- | --- |
| Does memalloc explain the +14.9% RSS? | **No.** Tracked live heap is only ~23% of the gap; **alloc-space is lower on 3.15 (−16.5%)**, opposite of a “memalloc is blowing up” story. | verified |
| Is advertising “profiling works on 3.15” blocked by this RSS delta? | **No for functional claim.** Profiler starts, sample types populate, asyncio meta OK, 0 errors, req parity. | verified functional signals |
| Must shipping wait on a memalloc fix? | **No.** Wrong lever for the observed RSS. | inferred from verified totals |
| Is this a known 3.15 runtime cost unrelated to our memalloc path? | **Plausible, unproven.** Remaining ~77% of RSS has no attribution here (no profiler-off control, no memray). | guess / unknown |
| Correctness vs performance readiness | **Correctness/functional: ready to advertise with caveats.** **Performance/memory parity: not ready to claim** — caveat the RSS until profiler-off + repeats. | judgment on verified data |

**Disagree early:** Treating “prioritize memalloc” as the gate confuses a real process-RSS surprise with a sample type that does **not** show elevated allocation volume on B.

### Still unknown (would settle it)

1. Profiler-**off** A/B on both Pythons (splits runtime vs profiler RSS).
2. Memray / allocator RSS breakdown for the ~77% outside `heap-space`.
3. Repeated soaks / staging load (single 90s laptop).
4. Lock-contended workload (current lock types empty).
5. Latency p50/p95 (never recorded).

Canvas (side-panel artifact): workspace `canvases/local-314v315-smoke-ab.canvas.tsx`.

---

## What a reader should **not** conclude

- Not a staging / production readiness gate.
- Not a statistically repeated experiment.
- Not “3.15 is +15% memory” in general — only this workload, this tip, this laptop, profiling **on**.
- Not “profiler capture CPU is zero” — that was a harness field-name bug.
- Not latency parity — latency was never measured.
- Not “B has worse locks” — lock sample types were empty on **both** sides.
- Not “memalloc regresses on 3.15” — alloc-space/samples are **lower** on B; heap-space only bounds ~¼ of RSS.
- Experimental `smoke_ab/local_314v315` only proved boot + pprof write; this harness’s RSS/CPU numbers are **extra** instrumentation, still smoke-grade.

## Harness bug fix (after this run)

`scripts/local_ab_314v315/run.sh` now prefers `sample_capture_cpu_time_us` (falls back to the old name). Saved `runs/20260921T033620Z/delta_table.json` remains the **pre-fix** publish.
