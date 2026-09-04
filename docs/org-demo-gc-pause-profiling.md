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
2. **Open CompView A vs B** — start with smoke **eBPF CPU Time** link below. Confirm **Compare by = CPU Time (host eBPF profiler)**.
3. **Search stacks** — filter `GCCollector`, then `_on_gc`, then `gc.collect`.
   - **Arm A:** no GC frames
   - **Arm B:** `GCCollector` / `_on_gc` / `gc.collect[gen=N]` appear
4. **Say:** opt-in flag `DD_PROFILING_GC_ENABLED=true`; default off. Overhead ceiling ~**0.01%** on eBPF CPU (shape-only CompView; not an RC gate).
5. **Optional — wall time** — open wall-time link; set **Compare by = Wall Time**. Automated grading is **NO-DATA** for GC frames here — eyeball manually.
6. **Optional — metrics slide** — if no live staging: Metrics → `runtime.python.gc.pause.time` (#19937). Separate from flame proof.
7. **Future (10s)** — after #2442: **GC Time** tab / `gc-time` Compare-by dimension; today we show GC work via eBPF CPU frames.

---

## Talking points

| Show | Say |
|------|-----|
| A-side empty search | "Without the flag, GC is a blind spot." |
| B-side GC frames | "Same workload, same SHA — only the flag flipped." |
| eBPF CPU | "Best automated A/B signal; GC shows up as real CPU during STW." |
| Wall time | "Frames can appear, but grader can't score them yet — manual only." |
| #19937 metrics | "Fleet-level pause budget even when profiles are off." |
| #2442 | "Unlocks first-class GC Time dimension instead of inferring from CPU." |

---

## Links (pinned CompView windows)

### Smoke — `rapid_python_http_smoke_test`

- [eBPF CPU Time](https://ddstaging.datadoghq.com/profiling/comparison?query=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gcs-b&compare_query_A=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gcs-a&compare_query_B=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gcs-b&compare_start_A=1788119889899&compare_start_B=1788119889899&compare_end_A=1788127116000&compare_end_B=1788127116000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=ebpf-cpu-time&from_ts=1788119889899&to_ts=1788127116000&live=false) — **primary demo** (B-only `GCCollector` / `_on_gc`)
- [Wall Time](https://ddstaging.datadoghq.com/profiling/comparison?query=service%3Arapid-td-ab-td19940-gcs-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_query_A=service%3Arapid-td-ab-td19940-gcs-a%20version%3Aab-a-1e53f618c22c%20env%3Astaging&compare_query_B=service%3Arapid-td-ab-td19940-gcs-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_start_A=1788119889899&compare_start_B=1788119889899&compare_end_A=1788127116000&compare_end_B=1788127116000&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=wall-time&from_ts=1788119889899&to_ts=1788127116000&live=false) — manual eyeball only

### ai_gateway 3h — cleaner parity

- [eBPF CPU Time](https://ddstaging.datadoghq.com/profiling/comparison?query=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gca-b&compare_query_A=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gca-a&compare_query_B=kube_cluster_name%3Agizmo%20container_name%3Arapid-td-ab-td19940-gca-b&compare_start_A=1788125373364&compare_start_B=1788125167369&compare_end_A=1788135967369&compare_end_B=1788135967369&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=ebpf-cpu-time&from_ts=1788125167369&to_ts=1788135967369&live=false)
- [Wall Time](https://ddstaging.datadoghq.com/profiling/comparison?query=service%3Arapid-td-ab-td19940-gca-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_query_A=service%3Arapid-td-ab-td19940-gca-a%20version%3Aab-a-1e53f618c22c%20env%3Astaging&compare_query_B=service%3Arapid-td-ab-td19940-gca-b%20version%3Aab-b-1e53f618c22c%20env%3Astaging&compare_start_A=1788125373364&compare_start_B=1788125167369&compare_end_A=1788135967369&compare_end_B=1788135967369&compareValuesMode=relative&comparisonViz=table&group_by=line&my_code=disabled&profile_type=wall-time&from_ts=1788125167369&to_ts=1788135967369&live=false) — manual eyeball only

**PRs:** [#19937](https://github.com/DataDog/dd-trace-py/pull/19937) · [#19940](https://github.com/DataDog/dd-trace-py/pull/19940) · [libdatadog#2442](https://github.com/DataDog/libdatadog/pull/2442)
