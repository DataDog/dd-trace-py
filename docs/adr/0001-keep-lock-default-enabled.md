# Decision: keep Lock profiler default-enabled (provisional → Q4)

## Meta

| Field | Value |
| :---- | :---- |
| **Status** | **Draft** — provisional keep Lock default ON until early-Q4 Lock isolation |
| **Authors / Deciders** | Vlad Scherbich, Taegyun Kim |
| **Start date** | 2026-09-17 |
| **Evidence vintage** | Thu Sep 17 16:56:13 EDT 2026 (−0400) |
| **Primary reviewers** | Profiling Python team |
| **Canonical** | This file (`docs/adr/0001-keep-lock-default-enabled.md`) |
| **Related** | Ported from [dd-trace-doe#487](https://github.com/DataDog/dd-trace-doe/pull/487) ([discussion](https://github.com/DataDog/dd-trace-doe/pull/487#discussion_r4073648808)); DoE lab evidence lives in dd-trace-doe |

## Decision

**Keep Lock default ON** (`DD_PROFILING_LOCK_ENABLED` / `ProfilingConfig.lock.enabled` = `true`) — **provisional until early-Q4 Lock isolation.**

**Measurand:** Lab DoE = profiling on/off (full-stack; capacity under Lock-heavy load — **not** Lock tax). Staging `ai_gateway` = Lock on/off at `profiling=true` (early Lock-isolated process cost, one service).

Full-stack DoE Δ cannot be charged to Lock and would not be reclaimed by flipping Lock alone. Early `ai_gateway` Lock ON/OFF @ `profiling=true` shows a small process-cost tax on one service (~+2.2% CPU / ~+0.71pp; ~+0.6% RSS / ~+4 MiB), which supports the keep-ON decision.

**Q4 gate:** individual-profiler isolation in the DoE harness; measure Lock-on vs Lock-off at `profiling=true`; revisit / revert the default if Lock-isolated numbers dictate.

## DoE results (full-stack `profiling` false→true)

Settings: FastAPI 0.116.1 / asyncio / Python 3.13 / library `4.15.0rc2`, 8 workers, zero lock-hold time, 10 replicates where possible. Baseline at 600 requests/s with lock-churn densities 0/50/200/500; stress runs use a wider CPU set (high-load at 600 rps; midpoint at 500 rps; reduced-RPS at 300 rps). Lock-ops density: 0 = lock-idle; 50/200/500 = light / moderate / heavy churn.

| Run | ops | scenario | Δ p50 latency | Δ CPU | n (off/on) |
|---|---:|---|---:|---:|---|
| Baseline | 0 | lock-idle | +1.0% (~+0.07 ms) | +14.1pp | 10/10 |
| Baseline | 50 | light churn | +3.5% (~+0.24 ms) | +19.0pp | 10/10 |
| Baseline | 200 | moderate churn | +7.4% (~+0.52 ms) | +29.0pp | 10/6 |
| High-load | 200 | stress @600 rps | +7.4% (~+0.52 ms) | +30.6pp | 10/9 |
| Reduced-RPS | 500 | stress @300 rps | +15.2% (~+1.11 ms) | +30.3pp (~+71%) | 10/10 |

At ops=0 we treat the profiler-on tax as a floor (optimistic — some Lock cost may already be in it). ops=500 only stays measurable if we back off request rate. Other workload archetypes show similar full-stack CPU bumps under profiling.

## ai_gateway Lock ON/OFF (Lock-isolated)

Same wheel both sides · `DD_PROFILING_LOCK_ENABLED` false→true @ **`profiling=true`**.

| Metric | Lock OFF | Lock ON | Δ |
|---|---:|---:|---:|
| CPU% (process average) | 32.84 | 33.55 | **~+2.2% (~+0.71pp)** |
| RSS (average) | 749 MiB | 753 MiB | **~+0.6% (~+4 MiB)** |
| lock acquisitions/s | 0 | 2.34 | visibility (not cost) |
| trace errors / HTTP 5xx | 0 | 0 | flat |

**CompView** (ai_gateway Test Drive, Lock ON vs OFF):

- [CPU Time (Python)](https://ddstaging.datadoghq.com/profiling/comparison?query=service%3Arapid-td-ab-lockonoff-ai0916e-b%20version%3Aab-b-eb8bb1fddc01%20env%3Astaging&compare_query_A=service%3Arapid-td-ab-lockonoff-ai0916e-a%20version%3Aab-a-eb8bb1fddc01%20env%3Astaging&compare_query_B=service%3Arapid-td-ab-lockonoff-ai0916e-b%20version%3Aab-b-eb8bb1fddc01%20env%3Astaging&compare_start_A=1789587570183&compare_start_B=1789587739188&compare_end_A=1789591315000&compare_end_B=1789591315000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=cpu-time&from_ts=1789587570183&to_ts=1789591315000&live=false)
- [CPU Time (eBPF)](https://ddstaging.datadoghq.com/profiling/comparison?query=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-lockonoff-ai0916e-b&compare_query_A=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-lockonoff-ai0916e-a&compare_query_B=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-lockonoff-ai0916e-b&compare_start_A=1789587570183&compare_start_B=1789587739188&compare_end_A=1789591315000&compare_end_B=1789591315000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=ebpf-cpu-time&from_ts=1789587570183&to_ts=1789591315000&live=false)
- [Lock Wait Time](https://ddstaging.datadoghq.com/profiling/comparison?query=service%3Arapid-td-ab-lockonoff-ai0916e-b%20version%3Aab-b-eb8bb1fddc01%20env%3Astaging&compare_query_A=service%3Arapid-td-ab-lockonoff-ai0916e-a%20version%3Aab-a-eb8bb1fddc01%20env%3Astaging&compare_query_B=service%3Arapid-td-ab-lockonoff-ai0916e-b%20version%3Aab-b-eb8bb1fddc01%20env%3Astaging&compare_start_A=1789587570183&compare_start_B=1789587739188&compare_end_A=1789591315000&compare_end_B=1789591315000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=lock-acquire-time&from_ts=1789587570183&to_ts=1789591315000&live=false)

**Conclusion:** Supports provisional keep-ON.

## Consequences

- **Now:** leave default ON; opt-out via `DD_PROFILING_LOCK_ENABLED=false` for extreme churn / tight CPU.
- **Early Q4:** Lock-on/off @ `profiling=true` in DoE harness → revisit / possible revert.
- **DoE ops:** give profiling=true enough CPUs for the worker count; shape latency request rate (≤~300 rps on this host) under baked-in lock churn.
- **Follow-ups:** (1) Q4 isolation gate; (2) ops=0 floor on a wider CPU set; (3) land archetype harness on `main`; (4) profiling stability under high churn (unstable at 600 rps); (5) longer lock-hold time and a shared-lock contention recipe (today’s zero hold time is acquire/release throughput only).

## What would reverse this

1. **Primary:** Q4 Lock-isolated DoE cost too high at a clean measurable envelope → revert default OFF.
2. Broader Lock-only study (beyond ai_gateway) above ~5 CPU percentage points or ~2 percentage points p50 latency at a clean envelope.
3. ops=0 floor assumption fails (fixed cost not roughly constant, or residual Lock in floor large enough to invalidate the bound).
4. Production / customer Lock overhead at realistic density.

Stability note (does **not** reopen Lock default): ops=500 was unmeasurable at 600 rps; reduced request rate restored measurability without changing Lock config.

## Open questions

1. Still unknown what “extreme” means for real customers — what lock-ops / CPU band should we point people at when we say opt out?
2. Is the solid 10/10 ops=500 envelope basically ≤~300 rps on this host, with partial results still usable up toward ~500?
3. What’s the exact Q4 harness for Lock-on/off at profiling=true — same DoE shape, or something else?

## Sources

DoE lab runs (dd-trace-doe): baseline `doedata-lock-official-20260911T011217Z`; high-load `doedata-lock-official-hiops-20260911T143126Z`; midpoint `doedata-lock-official-ops500-rps500-20260914T145959Z`; reduced-RPS `doedata-lock-official-ops500-polish-20260911T154237Z`. ai_gateway Test Drive Lock ON/OFF: `lockonoff-ai0916e` / `20260916T185016Z` (library wheel `eb8bb1fd`). Library under DoE: `4.15.0rc2` (`1a6351b49abc4dca5de9bd3e09e266c19bf35447`).
