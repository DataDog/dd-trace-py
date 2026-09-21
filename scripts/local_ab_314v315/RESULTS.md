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
| pprof sample types (`cpu-time`, `alloc-space`, `heap-space`, domains `mem`/`obj`) | **Not** in harness table | Present on disk; used only in this note’s RSS attribution check. |
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
| Live heap tracked by profiler (`heap-space`) | **Partial, weak** | `go tool pprof -sample_index=heap-space` on profiles 1–7: A ~7.6–10.6 MB; B ~7.5–19.8 MB (one spike on B #2). Domain split exists (`allocator domain: mem` vs `obj` via `-tags`). Order of magnitude of tracked live heap (~8–20 MB) is **smaller than** the ~14 MiB process RSS gap and is **not** a full process RSS accounting. |
| `alloc-space` cumulative | **Not usable for RSS** | Totals are TB-scale sampled allocation volume per 15 s window (workload churn via `alloc_pressure` / `pure_mem`), not resident set. |
| 3.15 allocator / pymalloc behavior | **Plausible, unproven** | Profiles show `mem` and `obj` domains on both sides with `memory.mem_domain_enabled=true`, but **no** controlled allocator A/B and **no** memray. Cannot attribute X MiB of the +14.9% to pymalloc vs interpreter vs profiler. |
| Profiler overhead vs app | **Unknown split** | Single PID RSS; no profiler-off control run. |

**Bottom line:** The +~15% RSS is a **real process-RSS difference** on this soak (**verified** `proc_metrics.csv`). Profiles **cannot** support a concrete fraction attributed to allocator vs profiler vs live objects (**verified** gap between `heap-space` totals and process RSS; **unknown** causal split). Do not cite “3.15 allocator” or “profiler samples” as the cause from this run alone.

---

## What a reader should **not** conclude

- Not a staging / production readiness gate.
- Not a statistically repeated experiment.
- Not “3.15 is +15% memory” in general — only this workload, this tip, this laptop, profiling **on**.
- Not “profiler capture CPU is zero” — that was a harness field-name bug.
- Not latency parity — latency was never measured.
- Experimental `smoke_ab/local_314v315` only proved boot + pprof write; this harness’s RSS/CPU numbers are **extra** instrumentation, still smoke-grade.

## Harness bugfix (after this run)

`scripts/local_ab_314v315/run.sh` now prefers `sample_capture_cpu_time_us` (falls back to the old name). Saved `runs/20260921T033620Z/delta_table.json` remains the **pre-fix** publish.
