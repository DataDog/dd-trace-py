# Python 3.15 profiling support (dd-trace-py)

**Status:** Accepted with caveats · **Date:** 2026-09-21 · **Related:** [#17817](https://github.com/DataDog/dd-trace-py/issues/17817), [PROF-14084](https://datadoghq.atlassian.net/browse/PROF-14084), [#19272](https://github.com/DataDog/dd-trace-py/pull/19272), [#20450](https://github.com/DataDog/dd-trace-py/pull/20450)

## Summary

**Functional profiling on CPython 3.15.0a7 works.** Memory parity with 3.14 is **not** claimed; GA / broad advertising is **not yet**. Evidence below is **a7-only** — [3.15.0rc2](https://peps.python.org/pep-0790/) shipped 2026-09-01; local re-soak on rc2 **not done**.

| Claim | Result (local AB, 2026-09-21, harness tip [`049961374f`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315)) |
| :---- | :---- |
| Smoke ON RSS (3.15.0a7 vs 3.14, 90 s) | **+14.9%** (90.9 → 104.5 MiB); req parity, 0 errors |
| `alloc-space` | **−16.5%** on 3.15.0a7; locks empty both sides |
| ON-gap attribution | **~53%** runtime / **~47%** profiler interaction (~24% tracked heap-space; ~24% residual) |
| Async AB | RSS **~parity** (−1.3%); `asyncio_task_count` **~110**; 0 errors |
| Wrap vs monitoring | 3.14 `wrap` / 3.15.0a7 `sys.monitoring` — **PASS** |

Harness + writeups + `runs/`: branch `vlad/chore-local-ab-314v315` @ [`049961374f`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315) — [`RESULTS.md`](https://github.com/DataDog/dd-trace-py/blob/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315/RESULTS.md), [`RESULTS_ASYNC.md`](https://github.com/DataDog/dd-trace-py/blob/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315/RESULTS_ASYNC.md). Staging soak incomplete — do not cite staging RSS/CPU parity.

## Decision

1. **Declare functional support on 3.15.0a7** (not rc2): profiler starts, pprof written, sample types present.
2. **Do not gate** that claim on memalloc — `alloc-space` already **−16.5%**; heap-space is only ~23% of the RSS gap.
3. **Caveat process RSS** (+14.9% smoke ON); attribute before optimizing.
4. Staging / DoE / Rapid remain open — not completed gates. Re-soak on rc2 before broadening the claim.

## Status

| Claim | Status |
| :---- | :----- |
| Functional profiling on 3.15.0a7 | **Works** |
| Same claim on 3.15.0rc2 | **Not re-soaked** |
| Memory parity vs 3.14 | **Not claimed** |
| Broad advertising (docs/marketing) | **Not yet** |
| Gate on memalloc before functional claim | **Rejected** |

## Evidence

Verified = named artifact on harness tip [`049961374f`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315); inferred = arithmetic on verified. `/tmp/...` paths below are the original laptop run dirs; durable copies live under `scripts/local_ab_314v315/runs/` on that tip.

### A. Profiler on — smoke A/B

[`runs/20260921T033620Z`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315/runs/20260921T033620Z) · A 3.14.6 / B 3.15.0a7 · 90 s · concurrency 2

| Metric | A → B |
| :----- | :---- |
| Req | 449 → 456 (+1.6%), **0** errors |
| RSS mean | 90.9 → 104.5 MiB (**+14.9%**) |
| `heap-space` | 8.56 → 11.77 MiB (+3.21, ~23% of RSS gap) |
| `alloc-space` | **−16.5%** on B |
| Locks | types present, **0 / 0** |

### A2. Async long-lived loop A/B

[`runs/20260921T175559Z_async`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315/runs/20260921T175559Z_async) · concurrency 4 · harness `049961374f`

| Metric | A → B |
| :----- | :---- |
| Req | 37734 → 37775 (+0.1%), **0** errors |
| RSS mean | 58.7 → 58.0 MiB (**−1.3%**) |
| `asyncio_task_count` | 109.3 → 111.4 |
| Named tasks | `long-pool-*` both sides; dedicated asyncio sample type **absent** |

### A3. Wrap vs `sys.monitoring` probe

`GET /hook_path` · [`runs/20260921T175546Z_async_probe`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315/runs/20260921T175546Z_async_probe) · also asserted in soak A2.

| Python | Result |
| :----- | :----- |
| 3.14.6 | `observed_path=wrap` — **PASS** |
| 3.15.0a7 | `observed_path=monitoring`, tool_id=3 — **PASS** |

### B. Profiler off + attribution

`PROFILING=0` · [`runs/20260921T162409Z_profoff`](https://github.com/DataDog/dd-trace-py/tree/049961374ff3701957fc77ac6d94f6785649ffa7/scripts/local_ab_314v315/runs/20260921T162409Z_profoff) · RSS 52.7 → 59.9 MiB (**+13.6%** / +7.19 MiB).

| Bucket of \(G_{on}=+13.59\) MiB | MiB | % |
| :---- | --: | --: |
| Runtime 3.15 (\(G_{off}\)) | +7.19 | **52.9%** |
| Profiler-on interaction | +6.40 | **47.1%** (inferred) |
| …tracked heap-space | +3.21 | 23.6% |
| Residual unknown | +3.19 | **23.5%** (inferred) |

### Other gates

* prof-correctness `python_*_3.15` + compare **success** ([Actions 35619836537](https://github.com/DataDog/prof-correctness/actions/runs/35619836537)) — CI ≠ soak.
* Staging Rapid / DoE: `BUILD_WEDGED`, missing wheels, auth — **no** staging parity claim.
* Stack: [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) / [#20450](https://github.com/DataDog/dd-trace-py/pull/20450) open; natives / wrap trampoline merged.

## Consequences / open risks

* Docs may say profiling **functions** on 3.15 with an **RSS caveat** once packaging lands; priority is RSS attribution + staging clear, not a memalloc rewrite.
* Single-run laptop soaks; no local latency; evidence is **3.15.0a7** (rc2 exists, not re-soaked); asyncio parent/child link and dedicated sample type still open.
* Track [PEP 790](https://peps.python.org/pep-0790/) — land engraver images within days of each RC/final. As of Mon Sep 21 14:14 EDT 2026 (−0400): rc2 (2026-09-01) still absent from images master (`python/3.15*`); only draft images#11732.
