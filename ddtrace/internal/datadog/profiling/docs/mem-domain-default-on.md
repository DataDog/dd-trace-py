# Decision: accept MEM-domain heap hook default-on for 4.15

Status: **Draft**
Deciders: Florian Engelhardt (@realFlowControl), Tn
Date: 2026-09-11
Evidence vintage (local clock): Fri Sep 11 16:21:28 EDT 2026 (−0400)

`dd-trace-py` has no numbered ADR series. This record lives with the profiler design notes (`ddtrace/internal/datadog/profiling/docs/`). Moved from [dd-trace-doe #488](https://github.com/DataDog/dd-trace-doe/pull/488) (wrong repo).

## Problem

The MEM-domain heap hook (`DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED` / `config.memory.mem_domain_enabled`) has shipped since 4.11, default **false**. [#19999](https://github.com/DataDog/dd-trace-py/pull/19999) flipped the default to **true** on `dd-trace-py` `main` (merged 2026-09-03). `v4.14.0` still ships `false`. `v4.15.0rc1` was tagged the day before that merge and does **not** contain it.

Should the next **4.15** tag accept that default-on, or revert / hold for a 4.14.x backport or more evidence?

## Decision (draft)

**Accept default-on on the next tagged 4.15. Do not backport to 4.14.x.**

Leave `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED` / `config.memory.mem_domain_enabled` default `true` on the 4.15 line. Opt-out stays `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=false`.

Epistemic status: **inferred** from the leftover Rapid same-build print below (the only hook-cost number), plus already-on prod twins that show fleets living with the hook. This is **not** a DoE ON/OFF matrix and **not** a claim that MEM saved CPU, RSS, or money.

## Scope — ship A only

| Layer | This record | Not this record |
|---|---|---|
| **A — hook default** | Accept `mem_domain_enabled` default `true` on 4.15 | 4.14.x backport |
| **B — MEM% UI** | — | [`dd-trace-py` #19473](https://github.com/DataDog/dd-trace-py/pull/19473) `allocatorDomain` label; CompView does not advertise it |
| **C — lab / opt-stack** | — | DoE MEM-domain bench, opt-stack |

A is the tracer default. B is a pprof label in front of a UI that is not shipping here. C is a different measurement program. Do not bundle them.

## Methodology

Two kinds of number. They are not interchangeable.

1. **Same-build hook cost.** One image, only the env var differs, load matched. This is the cost print. We have **one**: leftover Rapid / ai_gateway `5dc0569`.
2. **Already-on / living with it.** ExtraEnv has been true on some DCs for days. An off twin in another DC is **not** a flag flip on the same pod. Use it to say the fleet did not melt — not to quote a hook tax or a savings.

Traffic-normalized CPU on the twins is a **caveat**, not a second cost print. RSS-per-rps is refused (RSS is already flat; dividing it by a quieter DC invents a tax).

## Evidence: leftover Rapid `5dc0569` — same-build hook cost

**The only cost print.** Staging Test Drive, same wheel, A off / B on.

**Run:** `staging_ab/runs/mem5dc0569_reuse_20260901T172428Z` on `workspace-vlad-ws4` (laptop `experimental` has no copy of the run dir).
**Sources (verified):** [#19999](https://github.com/DataDog/dd-trace-py/pull/19999) AB table (same cells); board writeup `mem-prod-ga-evidence` (process CPU / RSS / HTTP from that run’s TLDR).
**Window:** Tue Sep 1 1:28–2:28 PM EDT 2026 (−0400).

The leftover cells below are **human-graded** from that run. The DoE harness marked leftover `5dc0569` **CONFOUNDED** — do not cite a harness PASS.

| Lane | A off | B on | Δ | Kind | Read as |
|---|---:|---:|---:|---|---|
| Process CPU | 59.4% | 60.8% | **+2.2%** | same-build hook on vs off | Flat-to-tiny up. `runtime.python.cpu.percent` avg. |
| Process RSS | 822 MiB | 819 MiB | **−0.4%** | same-build hook on vs off | Flat. |
| HTTP drive | 85555 ok / 0 err | 85512 ok / 0 err | −0.05% | same-build hook on vs off | Matched load. 5xx 0 on both. |

This is fixture load on a leftover canary, **not** live Rapid QPS. It is still the only same-image, hook-only, load-matched print we have. Process CPU is the trust column. That +2.2% is why default-on is acceptable, not why it is free.

`383ed019` is **not** a second leftover. HTTP on that drive was unmatched (B +16%). Do not quote its CPU/RSS as cost.

## Evidence: prod living with the hook

Already-on twins and already-on fleets. **Not hook cost.** Caption: quiet enough that we are not watching a melt. Not a default-on savings proof.

**ds-metrics window (verified):** same image `v135449329-c3a6d1d7`, Fri Sep 4 15:10 → Mon Sep 7 09:00 EDT 2026 (−0400) (65.8 h after settle). us3 / ap2 extraEnv on; us5 / ap1 off. Twin still split as of the Fri Sep 11 11:23 EDT board check (us3/ap2 ON, us5/ap1 OFF). us1 (~600 pods) is **not** this twin — do not grade it as off-vs-on.

CPU on ds-metrics is **container cores per `delancie-worker`** (`kubernetes.cpu.usage.total` nanocores / 1e9). `runtime.python.cpu.percent` on that service was garbage in this pull (negative thousands) and is not used.

| Target | Kind | CPU | RSS | Traffic | Read as |
|---|---|---|---|---|---|
| leftover Rapid `5dc0569` | same-build on vs off | +2.2% | −0.4% | HTTP matched | Only clean hook-cost. Repeated from the table above so the two kinds sit next to each other. |
| ds-metrics us3 vs us5 | already on vs off twin | 1.03 vs 1.40 cores/pod | 1366 vs 1388 MiB (**−1.6%**) | grpc 2.74 vs 5.92/s (**2.16×** off) | RSS flat. Traffic-norm fleet cores/(grpc/s) is **1.60× ON** — quieter DC still pays a fixed floor, **not** a hook tax. |
| ds-metrics ap2 vs ap1 | already on vs off twin | 0.45 vs 0.64 cores/pod | 1423 vs 1426 MiB (**−0.2%**) | grpc 0.37 vs 1.56/s | RSS flat. Same kind as us3/us5; thinner pair. |
| LAP all 7 DCs | already on, no off twin | 1.01% fleet | 711 MiB fleet | kafka 17/s | Last 24 h ending Thu Sep 3 3:58 PM EDT, SHA `v134320883-441931aa`. All seven extraEnv true. No off twin — do not invent one. |
| ai_gateway Rapid prod | already on · SHA mix | — | — | — | Git extraEnv true. Two SHAs in the Sep 2–3 window. **Do not subtract SHAs.** Leftover `5dc0569` remains the ai_gateway cost print. |

**Traffic-norm caveat (us3 vs us5 only).** us5 carried 2.16× the grpc. Raw CPU is higher on the **off** twin because it is busier. Dividing fleet cores by grpc/s leaves ON at **1.60×** per request. That is amortization of a quieter DC (a floor you still pay when traffic is thin), not “the hook costs 60%.” RSS-per-rps on the same pair is **refused**: RSS is already −1.6%; dividing a flat memory number by 2.16× traffic makes ON look ~2× worse and is not a measurement of the hook.

us5 restarts in that window were on the **OFF** twin. They are not a MEM tax.

## What this evidence does not support

Do not put any of these in a default-on argument:

- Friday Rapid **−13.8%** or Sunday **+277%** as hook cost (wrong window / wrong kind).
- `383ed019` HTTP **+16%** as a clean leftover (unmatched drive).
- CompView Δ% as dollars, as fleet cost, or as MEM-vs-OBJ (shape, not $; `allocatorDomain` not advertised).
- us5 restarts as MEM tax (off twin).
- Lock metrics (not in catalog on these pulls — omitted, not 0).
- dogweb **#3836** “rolled” (it did not).
- “MEM saved money” / Lin $ from any row above.
- Mon Aug 31 LAP or ds-metrics Δ% as hook cost (already-on **and** a new app SHA).

## Open gaps (do not paper over)

1. **No same-build off-vs-on on live Rapid QPS.** Leftover is fixture load. A prod freeze + same-digest flag flip was not done.
2. **Fixture load ≠ prod QPS.** +2.2% is the leftover drive, not a prediction of customer RPS.
3. **CompView `allocatorDomain` is not advertised.** We cannot split MEM vs OBJ in the UI from these profiles. That is ship B, not a blocker for A.

None of these reopen the 4.15 default on the evidence we have. They are why we do not call the hook free, and why we do not ship the MEM% UI in the same decision.

## Consequences

- **Product:** accept default-on on the next 4.15 tag (`v4.15.0` / later rc — not `v4.15.0rc1`, which predates #19999). Leave 4.14.x at default `false`. Opt-out: `DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=false`.
- **Do not** treat this record as enabling the hook — #19999 already landed that on `main`. This record is the GA justification.
- **Do not** ship #19473 / MEM% UI, or a DoE/opt-stack claim, as part of this decision.
- **Follow-ups** (none block 4.15 A):
  1. Same-build off-vs-on on live Rapid, if the service owners will deploy a same-digest flag flip. Nice; not a gate.
  2. Ship B (`allocatorDomain` advertised in CompView) when the backend/FE work is actually on.
  3. Field overlay expansion (ds-metrics eu1/us5/ap1) is a helm extraEnv decision, not the tracer default.

## What would reverse this decision

Any one of these reopens the 4.15 default — not a vibe, a trigger:

1. **A same-build on-vs-off** (leftover-clean: matched HTTP, one image, only the hook) showing process CPU well above the leftover +2.2% **or** a clear RSS climb, at production-like QPS.
2. **Customer or prod evidence** of MEM-hook overhead at realistic density that leftover + the twins cannot speak to.
3. **A decision to revert #19999 on `main`** before the 4.15 tag — that is a different PR; this record would then be withdrawn.

Absent a trigger, the default stays on for 4.15. The burden of proof sits with changing a default that is already on `main`, not with writing this down.

A live-Rapid same-build that never happens is **not** a reverse trigger. We already chose not to wait on that dance.

## Open questions

1. Which 4.15 tag actually cuts with #19999 (`rc2` vs GA) — release-engineering, not this record.
2. When (if ever) to run a same-digest Rapid prod flip. Does not block A.
3. When CompView will show `allocatorDomain` (ship B). Does not block A.
