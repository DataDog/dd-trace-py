# Python 3.15 profiling support (dd-trace-py)

## Meta

| Field | Value |
| :---- | :---- |
| **Status** | **Accepted with caveats** — functional profiling on CPython 3.15 is data-backed; memory parity and broad advertising are not |
| **Authors** | Vlad Scherbich (dd-trace-py profiling) |
| **Start date** | 2026-09-21 |
| **Vintage of evidence** | Mon Sep 21 12:19–~13:00 EDT 2026 (−0400); re-verify any live CI/staging claim before citing as current |
| **Primary reviewers** | Profiling Python team |
| **Related** | Tracker [#17817](https://github.com/DataDog/dd-trace-py/issues/17817) / parent [#17809](https://github.com/DataDog/dd-trace-py/issues/17809); asyncio monitoring [#19272](https://github.com/DataDog/dd-trace-py/pull/19272); Jira [PROF-14084](https://datadoghq.atlassian.net/browse/PROF-14084) |

### Publication

This ADR records a **data-backed declaration** that dd-trace-py continuous profiling **works on Python 3.15** for functional collection, with explicit separation from memory-parity and marketing-ready claims. It does **not** approve GA advertising of “zero RSS delta vs 3.14” or staging-grade overhead until the open risks below are closed.

## Decision

1. **Declare functional support:** On CPython 3.15 (validated locally on **3.15.0a7**), the continuous profiler **starts**, emits profiles, and populates expected sample types under the same tip used for the local smoke A/B (`822dd5a3fa…`, PR [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) stack lineage). Treat “profiling works on 3.15” as **true for functional/correctness advertising**, not as memory parity.
2. **Do not gate functional advertising on memalloc optimization.** Local profiler-on sample totals show **lower** `alloc-space` on 3.15 (−16.5%); tracked live `heap-space` explains only ~23% of the process-RSS gap. Memalloc is the wrong first lever for the RSS surprise.
3. **Caveat process RSS until attributed.** A single 90 s laptop soak with profiling on showed **+14.9%** mean process RSS (3.15 vs 3.14). A matched **profiler-off** control (same tip) shows **~53%** of that gap is **runtime 3.15** and **~47%** is **profiler-on interaction**; ~**23.5%** of the on-gap remains unexplained after subtracting runtime + tracked heap-space.
4. **Keep staging / DoE / Rapid bake as open proof paths**, not as completed gates. Several staging/ai_gateway attempts ended in `BUILD_WEDGED`, auth/entitlement walls, or wheel-missing failures; lab DoE had partial “ALL LEGS DONE” runs that must be cited by SHA, not as blanket production readiness.

## Status labels (use these words)

| Claim | Status | Notes |
| :---- | :----- | :---- |
| Functional profiling on 3.15 | **Works** | Profiler starts, pprof written, sample types present, 0 HTTP errors on local smoke |
| Memory parity vs 3.14 | **Works-with-caveats / not claimed** | Measurable RSS gap; see attribution |
| Ready to advertise broadly (docs/marketing “supported”) | **Not yet** | Needs repeats, staging soak without BUILD_WEDGED, and an honest RSS caveat in customer-facing copy |
| Gate on memalloc fix before advertising functional support | **Rejected** | Data contradicts the premise |

## Context

CPython 3.15 changes interpreter monitoring / wrapping surfaces (PEP 669 `sys.monitoring`, asyncio). dd-trace-py’s profiling-315 stack (notably [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) asyncio monitoring, natives/CI on earlier stack PRs, smoke-required CI [#20450](https://github.com/DataDog/dd-trace-py/pull/20450)) exists to keep continuous profiling viable. Customers and internal validation need a single place that separates **“does it profile?”** from **“is overhead acceptable?”**.

## Evidence

Epistemic labels: **verified** = read named artifact this session; **inferred** = arithmetic on verified; **extrapolated** = verified pushed beyond its window; **unknown** = not found / not measured.

### A. Local smoke A/B — profiler on

| Field | Value | Status |
| :---- | :---- | :----- |
| Harness | `scripts/local_ab_314v315/` on branch `vlad/chore-local-ab-314v315` | verified |
| `RUN_DIR` | `/tmp/local314v315_ddtracepy_20260921T033620Z` | verified |
| Tip | `822dd5a3fa158883b368965f081c97ccaa32c617` | verified `summary.json` |
| Pythons | A 3.14.6 · B 3.15.0a7 · 90 s · concurrency 2 | verified |
| RSS mean | 90.9 → 104.5 MiB (**+14.9%**, +13.6 MiB) | verified `proc_metrics.csv` |
| CPU mean | ~50.3% → ~50.1% (≈ flat) | verified |
| `heap-space` mean | 8.56 → 11.77 MiB (**+3.21 MiB**, ~23% of RSS gap) | verified pprof |
| `alloc-space` | −16.5% on B | verified |
| Locks | sample types present, **0 / 0** both sides | verified |
| Writeup | `scripts/local_ab_314v315/RESULTS.md` | verified |

### B. Local smoke A/B — profiler off (runtime-only control)

| Field | Value | Status |
| :---- | :---- | :----- |
| Flag | `PROFILING=0` in `scripts/local_ab_314v315/run.sh` | verified |
| Tip | same `822dd5a3fa…` (pinned `/tmp/dd-trace-py-822dd5a-profoff`) | verified |
| `RUN_DIR` | `/tmp/local314v315_profoff_20260921T162409Z` | verified |
| Vintage | 2026-09-21 12:24–12:31 EDT (−0400) | verified local clock |
| RSS mean A→B | 52.7 → 59.9 MiB (**+7.19 MiB, +13.6%**) | verified |
| CPU mean A→B | 42.7% → 39.2% | verified |
| `profiler_started` | false / false; 0 pprof | verified |

Attribution of on-gap \(G_{on}=+13.59\) MiB:

| Bucket | MiB | % of \(G_{on}\) | Status |
| :----- | --: | --------------: | :----- |
| Runtime 3.15 (\(G_{off}\)) | +7.19 | **52.9%** | verified |
| Profiler-on interaction | +6.40 | **47.1%** | inferred |
| …tracked heap-space (on-run) | +3.21 | 23.6% | verified |
| Residual unknown | +3.19 | **23.5%** | inferred |

### C. Sample-type deep dive (profiler on)

See RESULTS.md and canvas `local-314v315-smoke-ab.canvas.tsx`: locks empty; alloc-space down; heap vs RSS diverge; mem domain carries tracked heap increase. **Verified** from zstd + `go tool pprof`.

### D. Staging DoE / AB attempts (honest)

| Leg | Outcome | Status |
| :-- | :------ | :----- |
| Lab DoE 3.14 vs 3.15 (ws-3), tip family around `#19272` / `aaf3c070` | Transcript reports **ALL LEGS DONE** for at least one lab DoE; not re-opened as production gate here | **extrapolated** from conversation transcript; re-verify ws-3 artifacts before quoting overhead numbers |
| Staging Rapid smoke / `ai_gateway` ABs | Multiple campaigns **`BUILD_WEDGED`**, 0 pods, contaminated/REGRESSION, or Rapid bake / wheel-missing walls (e.g. `nh_aigw_pr3tip_*`, `smoke_pr19272_nopy*`) | **verified** via transcript citations + `staging_ab/AB_FAILURE_AUDIT.md` systemic classes |
| Auth / entitlement | Staging preflight often blocked on org-2 / callgraph / OAuth matrix | **verified** audit doc |

**Do not** claim staging RSS/CPU parity for 3.15 from this ADR.

### E. prof-correctness

| Item | Value | Status |
| :--- | :---- | :----- |
| Scenarios | `python_*_3.15` families present (cpu, asyncio, alloc, live_heap, mem_domain, lock, …) | verified listing |
| CI | Actions run [35619836537](https://github.com/DataDog/prof-correctness/actions/runs/35619836537) (2026-09-21): `python 3.15 / scenarios 1–4` **success**, `compare 3.14 vs 3.15` **success** | verified `gh run view` |
| Caveat | Gate jobs pin wheels / compare margins; success ≠ production soak | inferred |

### F. Unit / CI / product PRs

| Item | State (this session) | Status |
| :--- | :------------------- | :----- |
| [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) asyncio `sys.monitoring` | **OPEN** | verified `gh pr view` |
| [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) require cp315 wheels / lib_injection on 3.15 | **OPEN**, checks sampled passing | verified |
| [#19910](https://github.com/DataDog/dd-trace-py/pull/19910) wrap trampoline on 3.15 | **MERGED** | verified search |
| [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) natives on 3.15 | **MERGED** (stack history) | verified search |

### G. Engraver / wheel / Rapid bake

Only cite what is verified: missing cp315 wheels on some stack tips historically blocked staging TDs and prof-correctness Docker; engraver auto-bump noise appears in prof-correctness Actions. **No** claim that Rapid bake for 3.15 customer images is complete.

## Consequences

- Product / docs may say profiling **functions** on 3.15 with an **RSS caveat**, once release packaging lands.
- Engineering priority: close RSS attribution (profiler-off + memray/repeats), land open stack PRs, clear staging BUILD_WEDGED for a real 314-vs-315 soak — **not** a premature memalloc rewrite.
- DoE/staging harness failures remain operational debt documented in `AB_FAILURE_AUDIT.md`.

## Open risks

1. Single-run laptop soaks (on and off) — no statistical repeats.
2. Staging 3.14-vs-3.15 soak still incomplete (BUILD_WEDGED / wheels / Rapid).
3. Lock sample types empty on smoke corpus — lock behavior uncompared.
4. Latency never measured in local harness.
5. 3.15 still alpha/rc moving target (`3.15.0a7` in local AB).

## Declaration (copy-paste)

> **Python 3.15 continuous profiling for dd-trace-py works functionally** (profiler starts, profiles upload/write, sample types populate) on the tips and gates cited in this ADR. **Memory parity with 3.14 is not claimed.** Local profiler-on smoke showed ~+15% process RSS; the matched profiler-off control attributes **~53% to runtime 3.15** and **~47% to profiler-on interaction** (~24% of the on-gap visible as tracked heap-space; ~24% still residual/unknown). **Broad advertising / GA “supported” copy should wait** on repeats and a clean staging soak, and must keep an RSS caveat until parity is shown.
