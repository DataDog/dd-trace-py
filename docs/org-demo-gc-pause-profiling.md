# Python GC Pause Profiling — Org Demo (2–3 min)

**Problem:** Python stop-the-world GC pauses are real latency, but invisible in CPU/wall flame graphs today.

**What shipped / shipping:**
- [#19937](https://github.com/DataDog/dd-trace-py/pull/19937) — `runtime.python.gc.pause.time` / `.pause.max` runtime metrics (**approved, merging**)
- [#19940](https://github.com/DataDog/dd-trace-py/pull/19940) — GC stack frames in profiles (**opt-in**, needs [libdatadog#2442](https://github.com/DataDog/libdatadog/pull/2442) for `gc-time` dimension)

**Staging proof:** campaign `gc19940`, SHA `1e53f618c2`, Arm B only: `DD_PROFILING_GC_ENABLED=true` (default off).

---

## Setup

- Org: **ddstaging** · env: **staging**
- Versions: `ab-a-1e53f618c22c` (A) vs `ab-b-1e53f618c22c` (B)
- Services: `rapid-td-ab-td19940-gcs-{a,b}` (smoke) · `rapid-td-ab-td19940-gca-{a,b}` (ai_gateway 3h)

---

## Demo steps

1. **Hook (30s)** — "GC pauses stall every thread; until now we had no profile signal."
2. **Open CompView A vs B** — start with smoke **eBPF CPU Time** link below. Confirm **Compare by = CPU Time (host eBPF profiler)** and **visualization = Table** (not Flame Graph — links are pinned to `comparisonViz=table`).
3. **Search stacks (plain text, not facet filters)** — type `_on_gc` first, then `gc_monitor`. Do **not** use `function:GCCollector` or other facet syntax; that returns nothing on eBPF.
   - **Arm A:** no GC callback frames
   - **Arm B:** `_on_gc` / `gc_monitor` appear in the table
   - **Caveat:** B-only signal is tiny (~4 ms over ~45 s soak ≈ **0.01%**). A flame graph won't show a visible bar — use **Table** search or cite harness numbers.
   - **Frame semantics:** real profile frames are `gc.collect[gen=N]` on the **`gc-time`** dimension (after #2442). On eBPF CPU today you only see GC **callback** symbols (`_on_gc`, `gc_monitor`) as inference, not `gc.collect[gen=N]`.
4. **Say:** opt-in flag `DD_PROFILING_GC_ENABLED=true`; default off. Overhead ceiling ~**0.01%** on eBPF CPU (shape-only CompView; not an RC gate).
5. **Optional — wall time** — open wall-time link; set **Compare by = Wall Time**. Automated grading is **NO-DATA** for GC frames here — eyeball manually.
6. **Optional — metrics slide** — if no live staging: Metrics → `runtime.python.gc.pause.time` (#19937). Separate from flame proof.
7. **Future (10s)** — after #2442: **GC Time** tab / `gc-time` Compare-by dimension with `gc.collect[gen=N]` frames; today we show GC work via eBPF CPU callback symbols.

---

## Talking points

| Show | Say |
|------|-----|
| A-side empty search | "Without the flag, GC is a blind spot." |
| B-side `_on_gc` / `gc_monitor` in table | "Same workload, same SHA — only the flag flipped." |
| Table vs flame graph | "Signal is ~0.01% — table search finds it; flame graph won't show a bar." |
| eBPF CPU | "Best automated A/B signal today; GC shows up as callback CPU during STW." |
| Wall time | "Frames can appear, but grader can't score them yet — manual only." |
| #19937 metrics | "Fleet-level pause budget even when profiles are off." |
| #2442 | "Unlocks first-class GC Time dimension with `gc.collect[gen=N]` instead of inferring from eBPF callbacks." |

---

## Links (pinned CompView windows)

eBPF links use **`container_name` only** (no `service:` filter). All links pinned to **Table** view (`comparisonViz=table`).

### Smoke — `rapid_python_http_smoke_test`

- [eBPF CPU Time](https://ddstaging.datadoghq.com/profiling/comparison?query=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gcs-b&compare_query_A=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gcs-a&compare_query_B=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gcs-b&compare_start_A=1788119889899&compare_start_B=1788119889899&compare_end_A=1788127116000&compare_end_B=1788127116000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=ebpf-cpu-time&from_ts=1788119889899&to_ts=1788127116000&live=false) — **primary demo** (Table view; search `_on_gc` or `gc_monitor` on Arm B)
- [Wall Time](https://ddstaging.datadoghq.com/profiling/comparison?query=service%3Arapid-td-ab-td19940-gcs-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_query_A=service%3Arapid-td-ab-td19940-gcs-a%20version%3Aab-a-1e53f618c22c%20env%3Astaging&compare_query_B=service%3Arapid-td-ab-td19940-gcs-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_start_A=1788119889899&compare_start_B=1788119889899&compare_end_A=1788127116000&compare_end_B=1788127116000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=wall-time&from_ts=1788119889899&to_ts=1788127116000&live=false) — manual eyeball only

### ai_gateway 3h — cleaner parity

- [eBPF CPU Time](https://ddstaging.datadoghq.com/profiling/comparison?query=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gca-b&compare_query_A=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gca-a&compare_query_B=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gca-b&compare_start_A=1788125373364&compare_start_B=1788125167369&compare_end_A=1788135967369&compare_end_B=1788135967369&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=ebpf-cpu-time&from_ts=1788125167369&to_ts=1788135967369&live=false)
- [Wall Time](https://ddstaging.datadoghq.com/profiling/comparison?query=service%3Arapid-td-ab-td19940-gca-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_query_A=service%3Arapid-td-ab-td19940-gca-a%20version%3Aab-a-1e53f618c22c%20env%3Astaging&compare_query_B=service%3Arapid-td-ab-td19940-gca-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_start_A=1788125373364&compare_start_B=1788125167369&compare_end_A=1788135967369&compare_end_B=1788135967369&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=wall-time&from_ts=1788125167369&to_ts=1788135967369&live=false) — manual eyeball only

**PRs:** [#19937](https://github.com/DataDog/dd-trace-py/pull/19937) · [#19940](https://github.com/DataDog/dd-trace-py/pull/19940) · [libdatadog#2442](https://github.com/DataDog/libdatadog/pull/2442)
