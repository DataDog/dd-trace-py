# Python 3.15 profiling support (dd-trace-py)

## Meta

| Field | Value |
| :---- | :---- |
| **Status** | **Accepted with caveats** — functional profiling on CPython 3.15 is data-backed; memory parity and broad advertising are not |
| **Authors** | Vlad Scherbich (dd-trace-py profiling) |
| **Start date** | 2026-09-21 |
| **Figures vintage** | Evidence runs 2026-09-21 (smoke on ~03:36Z / off 12:24–12:31 EDT / async re-soak + wrap-vs-monitoring probe 13:55–13:58 EDT −0400); this ADR revision Mon Sep 21 13:58 EDT 2026 (−0400). Re-verify live CI/staging before citing as current. |
| **Primary reviewers** | Profiling Python team |
| **Canonical** | This file on branch `vlad/adr-py315-profiling`. Wiki republish **deferred** until ADRs land (prior draft page deleted; no wiki URL in this ADR). |
| **Related** | Tracker [#17817](https://github.com/DataDog/dd-trace-py/issues/17817) / [#17809](https://github.com/DataDog/dd-trace-py/issues/17809); Jira [PROF-14084](https://datadoghq.atlassian.net/browse/PROF-14084); PRs [#19272](https://github.com/DataDog/dd-trace-py/pull/19272), [#20450](https://github.com/DataDog/dd-trace-py/pull/20450); harness `vlad/chore-local-ab-314v315` @ `049961374f`; this ADR branch `vlad/adr-py315-profiling` |

### Publication

**Data-backed declaration:** continuous profiling **works on Python 3.15** for functional collection. Does **not** approve GA “zero RSS vs 3.14” or staging-grade overhead until open risks close. Medium of record is this git ADR; optional wiki stub later only.

## Decision

1. **Declare functional support** on CPython 3.15 (local **3.15.0a7**): profiler starts, emits profiles, expected sample types present — tip `822dd5a3fa…` (PR [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) lineage). Functional/correctness advertising **yes**; memory parity **no**.
2. **Do not gate** functional advertising on memalloc optimization. Profiler-on: `alloc-space` **−16.5%** on 3.15; tracked `heap-space` ≈ **23%** of process-RSS gap. Wrong first lever.
3. **Caveat process RSS.** Single 90 s laptop soak, profiling on: **+14.9%** mean RSS (3.15 vs 3.14). Matched profiler-off: **~53%** runtime / **~47%** profiler-on interaction; **~23.5%** residual after runtime + heap-space.
4. **Staging / DoE / Rapid** remain open proof paths — not completed gates (`BUILD_WEDGED`, auth, missing wheels).

## Status labels

| Claim | Status | Notes |
| :---- | :----- | :---- |
| Functional profiling on 3.15 | **Works** | Starts, pprof written, sample types present, 0 HTTP errors on smoke |
| Memory parity vs 3.14 | **Not claimed** | Measurable RSS gap; see attribution |
| Broad advertise (docs/marketing) | **Not yet** | Needs repeats + clean staging soak + RSS caveat in copy |
| Gate on memalloc before functional claim | **Rejected** | Data contradicts |

## Context

CPython 3.15 changes monitoring / wrapping (PEP 669, asyncio). Profiling-315 stack ([#19272](https://github.com/DataDog/dd-trace-py/pull/19272) asyncio, earlier natives/CI, [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) smoke-required) keeps continuous profiling viable. Separate **“does it profile?”** from **“is overhead acceptable?”**.

Local smoke is **weak** for asyncio / [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) depth (`asyncio_task_count` meta ≈ 3). Async long-lived-loop A/B plus live **wrap vs `sys.monitoring`** `/hook_path` probe are on record (see Evidence A2–A3).

## Evidence

Epistemic: **verified** = named artifact; **inferred** = arithmetic on verified; **extrapolated** = beyond window; **unknown** = not measured.

### A. Profiler on — local smoke A/B

| Field | Value | Status |
| :---- | :---- | :----- |
| Harness | `scripts/local_ab_314v315/` on `vlad/chore-local-ab-314v315` | verified |
| `RUN_DIR` | `/tmp/local314v315_ddtracepy_20260921T033620Z` | verified |
| In-repo copy | `scripts/local_ab_314v315/runs/20260921T033620Z/` (same branch) | verified |
| Tip | `822dd5a3fa158883b368965f081c97ccaa32c617` (parent `faae7e3` / [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)) | verified `summary.json` |
| Pythons | A 3.14.6 · B 3.15.0a7 · 90 s · concurrency 2 | verified |
| Req | 449 → 456 (+1.6%), **0** errors | verified `drive_stats.json` |
| RSS mean | 90.9 → 104.5 MiB (**+14.9%**, +13.6 MiB) | verified `proc_metrics.csv` |
| CPU mean | ~50.3% → ~50.1% (≈ flat) | verified |
| `heap-space` mean | 8.56 → 11.77 MiB (**+3.21 MiB**, ~23% of RSS gap) | verified pprof |
| `alloc-space` | **−16.5%** on B | verified |
| Locks | types present, **0 / 0** both sides | verified |
| Writeup | `scripts/local_ab_314v315/RESULTS.md` | verified |

### A2. Async long-lived loop A/B (profiler on)

Better [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) validator than smoke (elevated task count + named-task labels). **Not** a staging soak. Latest re-soak below; prior `…T173604Z` retained in RESULTS_ASYNC for Δ.

| Field | Value | Status |
| :---- | :---- | :----- |
| Harness | `run_async.sh` on `vlad/chore-local-ab-314v315` @ `049961374f` | verified |
| `RUN_DIR` | `/tmp/local314v315_async_20260921T175559Z` | verified |
| In-repo copy | `runs/20260921T175559Z_async/` | verified |
| Tip | `822dd5a3fa…` (parent `faae7e3` / [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)) | verified `summary.json` |
| Pythons | A 3.14.6 · B 3.15.0a7 · 90 s · concurrency 4 | verified |
| Req | 37734 → 37775 (+0.1%), **0** errors | verified `drive_stats.json` |
| RSS mean | 58.7 → 58.0 MiB (**−1.3%**) | verified |
| CPU mean | 22.7% → 24.0% | verified |
| `asyncio_task_count` mean | 109.3 → 111.4 (7 metas) | verified |
| Named tasks | `task name:[long-pool-*]` present both sides | verified mid pprof |
| Dedicated asyncio sample type | **absent** both | verified |
| Writeup | `scripts/local_ab_314v315/RESULTS_ASYNC.md` | verified |

### A3. Monitoring vs wrap probe (asyncio registration)

Live assert that **3.14 uses `wrap()`** and **3.15 uses `sys.monitoring`** for `create_task` / `TaskGroup.create_task` (PEP 669 path under [#19272](https://github.com/DataDog/dd-trace-py/pull/19272)).

| Field | Value | Status |
| :---- | :---- | :----- |
| Endpoint / gate | `GET /hook_path`; `PROBE_ONLY=1` or pre-soak in `run_async.sh` | verified |
| Probe-only `RUN_DIR` | `/tmp/local314v315_async_20260921T175546Z` | verified |
| In-repo copy | `runs/20260921T175546Z_async_probe/logs/hook_path_{A314,B315}.json` | verified |
| A 3.14.6 | `observed_path=wrap`, `create_task_wrapped=true`, tool_id null | verified **PASS** |
| B 3.15.0a7 | `observed_path=monitoring`, wrapped=false, `monitoring_tool_id=3`, handlers on both create_task sites | verified **PASS** |
| Also | asserted again inside soak `…T175559Z` before drive | verified |

### B. Profiler off — runtime control

| Field | Value | Status |
| :---- | :---- | :----- |
| Flag | `PROFILING=0` | verified |
| Tip | same `822dd5a3fa…` | verified |
| `RUN_DIR` | `/tmp/local314v315_profoff_20260921T162409Z` | verified |
| In-repo copy | `runs/20260921T162409Z_profoff/` | verified |
| Vintage | 2026-09-21 12:24–12:31 EDT (−0400) | verified |
| RSS mean A→B | 52.7 → 59.9 MiB (**+7.19 MiB, +13.6%**) | verified |
| CPU mean A→B | 42.7% → 39.2% | verified |
| Profiler | `profiler_started` false/false; 0 pprof | verified |

### Attribution of on-gap \(G_{on}=+13.59\) MiB

| Bucket | MiB | % of \(G_{on}\) | Status |
| :----- | --: | --------------: | :----- |
| Runtime 3.15 (\(G_{off}\)) | +7.19 | **52.9%** | verified |
| Profiler-on interaction | +6.40 | **47.1%** | inferred |
| …tracked heap-space (on-run) | +3.21 | 23.6% | verified |
| Residual unknown | +3.19 | **23.5%** | inferred |

### C. Sample types (on)

Locks empty both sides; alloc-space down; heap vs RSS diverge (`mem` domain carries tracked heap). **Verified** zstd + `go tool pprof`. Details in RESULTS.md.

### D. Staging DoE / AB (honest)

| Leg | Outcome | Status |
| :-- | :------ | :----- |
| Lab DoE 3.14 vs 3.15 (ws-3) | “ALL LEGS DONE” in transcript for ≥1 run — not a production gate | **extrapolated** |
| Staging Rapid / `ai_gateway` | `BUILD_WEDGED`, 0 pods, wheel-missing, contaminated/REGRESSION | **verified** audit classes |
| Auth / entitlement | Often blocked org-2 / callgraph / OAuth | **verified** audit |

**Do not** claim staging RSS/CPU parity for 3.15 here.

### E. prof-correctness

| Item | Value | Status |
| :--- | :---- | :----- |
| Scenarios | `python_*_3.15` families present | verified listing |
| CI | Actions [35619836537](https://github.com/DataDog/prof-correctness/actions/runs/35619836537) (2026-09-21): 3.15 scenarios + compare **success** | verified |
| Caveat | Gate success ≠ production soak | inferred |

### F. Product PRs (session snapshot)

| Item | State | Status |
| :--- | :---- | :----- |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) asyncio monitoring | **OPEN** | verified |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) require cp315 / lib_injection 3.15 | **OPEN** | verified |
| [#19910](https://github.com/DataDog/dd-trace-py/pull/19910) wrap trampoline | **MERGED** | verified |
| [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) natives 3.15 | **MERGED** | verified |

### G. Engraver / wheels / Rapid

Missing cp315 wheels historically blocked staging TDs and prof-correctness Docker. **No** claim Rapid bake for 3.15 customer images is complete.

## Consequences

- Docs may say profiling **functions** on 3.15 with an **RSS caveat** once packaging lands.
- Priority: RSS attribution (memray/repeats), land open stack PRs, clear staging `BUILD_WEDGED` — **not** a premature memalloc rewrite.
- Staging harness debt remains in `AB_FAILURE_AUDIT.md`.

## Open risks

1. Single-run laptop soaks (on + off) — no statistical repeats.
2. Staging 3.14-vs-3.15 soak incomplete (`BUILD_WEDGED` / wheels / Rapid / auth).
3. Lock sample types empty on smoke corpus.
4. Latency never measured locally.
5. 3.15 still moving (`3.15.0a7` in local AB).
6. Asyncio / [#19272]: wrap vs monitoring probe **done**; still no parent/child link check or dedicated asyncio sample type.

## Declaration (copy-paste)

> **Python 3.15 continuous profiling for dd-trace-py works functionally** (profiler starts, profiles write, sample types populate) on the tips and gates cited here. **Memory parity with 3.14 is not claimed.** Local profiler-on smoke: ~+15% process RSS; matched profiler-off attributes **~53% to runtime 3.15** and **~47% to profiler-on interaction** (~24% of on-gap as tracked heap-space; ~24% residual/unknown). **Broad advertising / GA “supported” copy should wait** on repeats and a clean staging soak, and must keep an RSS caveat until parity is shown.
