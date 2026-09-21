# Local 3.14 vs 3.15 **async** profiling A/B — verified results

Long-lived event-loop soak (not the ThreadingHTTPServer + per-request `asyncio.run` smoke). **Not** staging load, **not** multi-repeat.

Vintage (machine clock): **Mon Sep 21 13:55–13:58 EDT 2026 (−0400)** — probe `…T175546Z`, soak `…T175559Z`.

## How to re-run

```bash
# From a checkout that has scripts/local_ab_314v315/async_*.py + run_async.sh
# Point DDTRACE_SRC at #19272 tip (faae7e3 or current PR head) — not a chore-only tree.
DDTRACE_SRC=/tmp/dd-trace-py-822dd5a-profoff \  # or: git worktree add … faae7e3
  DURATION=90 CONCURRENCY=4 PROFILING=1 \
  ./scripts/local_ab_314v315/run_async.sh

REUSE_VENV=/tmp/local314v315_async_…/venvs DURATION=90 DDTRACE_SRC=… \
  ./scripts/local_ab_314v315/run_async.sh

# Monitoring-vs-wrap only (~2s after servers up; no soak):
DDTRACE_SRC=/tmp/dd-trace-py-822dd5a-profoff \
  REUSE_VENV=/tmp/local314v315_async_20260921T173604Z/venvs \
  PROBE_ONLY=1 PROFILING=1 \
  ./scripts/local_ab_314v315/run_async.sh
```

Ports default **18500 / 18501**. Requires `PY314_BIN` / `PY315_BIN` (same defaults as `run.sh`).

## Run identity (verified) — latest soak

| Field | Value |
| --- | --- |
| `RUN_DIR` | `/tmp/local314v315_async_20260921T175559Z` |
| In-repo copy | `runs/20260921T175559Z_async/` (summary, delta, proc, drive, `/stats` JSON, metadata — **no** `.pprof`) |
| Editable install tip | `822dd5a3fa158883b368965f081c97ccaa32c617` (`822dd5a^` = **`faae7e3b2b`** = GitHub #19272 HEAD when run) |
| A Python | `/opt/homebrew/bin/python3.14` → **3.14.6** |
| B Python | `~/.pyenv/versions/3.15.0a7/bin/python` → **3.15.0a7** |
| Duration | **90 s** |
| Concurrency | **4 workers / side** |
| Profiling | on (lock+memory, upload 15 s, local `OUTPUT_PPROF`) |
| Workload | `async_app.py`: one `asyncio.run` for process life; long-pool=24; continuous create_task / nest / TaskGroup churn; HTTP `/work` `/churn` `/fanout` |
| Prior soak (kept) | `/tmp/local314v315_async_20260921T173604Z` → `runs/20260921T173604Z_async/` |

---

## Comparison to smoke (`RESULTS.md`)

| Signal | Smoke (`app.py`) | Async latest (`…T175559Z`) | Status |
| --- | --- | --- | --- |
| Event loop | Per-request `asyncio.run` on ThreadingHTTPServer | **One loop for soak** | verified design |
| `asyncio_task_count` mean (pprof meta) | **3.00 / 3.00** | **109.29 / 111.43** (7 metas @ summary) | verified |
| Task names on stacks | Not exercised | **`task name:[long-pool-*]`** labels present both sides | verified `go tool pprof -raw` mid profile |
| Dedicated `asyncio` sample type | **absent** | **absent** | verified sample-type list |
| RSS mean Δ (B−A) | **+14.9%** | **−1.3%** (~parity) | verified |
| Errors | 0 | 0 | verified |

**Verdict:** this is a **better #19272 asyncio/monitoring validator than smoke**. Smoke only proved boot + flat meta `asyncio_task_count=3`. Async keeps task count elevated (~110) and emits named-task labels on CPU/wall samples on both 3.14 (wrap path) and 3.15 (sys.monitoring path). Soft gate `/hook_path` asserts wrap vs monitoring before soak.

---

## Verified metric table (latest soak)

Window: one 90 s concurrent drive; CPU/RSS from **n=88** `ps` rows/side; profiles every 15 s; table matches `delta_table.json` / `summary.json` at summary time (**7** `.pprof` / side; tree later grew to 8). **PASS** (0 errors; probe PASS before soak).

| Metric | A 3.14.6 | B 3.15.0a7 | Δ | Δ% | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| req total (90 s) | 37734 | 37775 | +41 | +0.1% | verified `drive_stats.json` |
| req/s | 419.3 | 419.7 | +0.5 | +0.1% | inferred |
| errors | 0 | 0 | 0 | 0% | verified |
| CPU% mean (`ps` 1 Hz) | 22.7% | 24.0% | +1.3 pp | +5.7% | verified |
| CPU% p95 | 28.7% | 30.7% | +2.0 pp | +7.0% | verified |
| RSS mean | 58.7 MiB | 58.0 MiB | −0.8 MiB | −1.3% | verified |
| RSS p95 | 62.2 MiB | 60.8 MiB | −1.4 MiB | −2.2% | verified |
| `asyncio_task_count` mean (7 metas) | **109.29** | **111.43** | +2.14 | +2.0% | verified metadata |
| `asyncio_task_count` max | 116 | 116 | 0 | 0% | verified |
| HTTP `/stats` task_count (post) | 27 | 27 | 0 | 0% | verified |
| HTTP named_tasks_n (post) | 25 | 25 | 0 | 0% | verified |
| `.pprof` @ summary | 7 | 7 | 0 | 0% | verified |
| `sample_count` sum | 25406 | 29233 | +3827 | +15.1% | verified |
| `sample_capture_cpu_time_us` sum | 478035 | 421772 | −56263 | −11.8% | verified |

Full 8-meta series (incl. post-kill): A `[114,114,113,116,116,110,82,82]` · B `[116,114,114,116,112,114,94,82]`.

### Δ vs prior soak (`…T173604Z`)

Same tip / interpreters / duration / concurrency. Single-run noise; not a regression claim.

| Metric | Prior A→B | Latest A→B | Note |
| --- | --- | --- | --- |
| req total | 38902 → 38339 (−1.4%) | 37734 → 37775 (+0.1%) | both ~0 errors |
| RSS mean | 60.3 → 60.5 (+0.3%) | 58.7 → 58.0 (−1.3%) | still ~parity |
| CPU% mean | 23.9 → 23.2 (−3.0%) | 22.7 → 24.0 (+5.7%) | sign flips; single-run |
| `asyncio_task_count` mean | 112.57 → 109.14 (−3.0%) | 109.29 → 111.43 (+2.0%) | both ~110 |

### Sample types (mid profile `.3`, zstd → `go tool pprof -raw`)

Present on **both** A and B (same list as smoke — no dedicated asyncio type):

`cpu-time`, `cpu-samples`, `wall-time`, `wall-samples`, `exception-samples`, `lock-acquire-wait`, `lock-acquire`, `lock-release-hold`, `lock-release`, `alloc-space`, `alloc-samples`, `heap-space`, `heap-live-samples`, (+ gpu schema zeros).

### Task-name labels (mid profile `.3`)

Both sides: thousands of samples labeled `task name:[long-pool-N]` (A mid count **3714**, B **4308**). Also `thread name:[asyncio_N]` for `to_thread` workers.

---

## Monitoring vs wrap probe (covered) — rerun PASS

| Field | Value |
| --- | --- |
| Endpoint | `GET /hook_path` on `async_app.py` |
| Gate | `run_async.sh` asserts A=`wrap`, B=`monitoring` when `PROFILING=1` (before soak) |
| Fast path | `PROBE_ONLY=1` exits after assert |
| Verified probe-only | `/tmp/local314v315_async_20260921T175546Z` → `runs/20260921T175546Z_async_probe/` |
| Also asserted | inside soak `…T175559Z` before drive |
| Prior probe (kept) | `/tmp/local314v315_async_20260921T174853Z` |
| Tip under test | `822dd5a3fa` (`faae7e3b2b` = #19272 HEAD parent) |

| Side | `observed_path` | `create_task_wrapped` | `monitoring_tool_id` | handlers | Status |
| --- | --- | --- | --- | --- | --- |
| A 3.14.6 | **wrap** | true | null | none | verified **PASS** |
| B 3.15.0a7 | **monitoring** | false | 3 | create_task + TaskGroup.create_task | verified **PASS** |

Meaning: on 3.15, `asyncio.tasks.create_task` / `TaskGroup.create_task` use `sys.monitoring` `PY_RETURN` (not `wrap()`); on 3.14 they stay `wrap()`'d. Other asyncio hooks remain wrap on both (by design in #19272).

Also covered in-PR by `tests/profiling/collector/test_asyncio_wrap_path.py` (unit). This harness probe is the soak-adjacent live check.

## Still missing for #19272

1. ~~Assert / diff that **3.15 uses sys.monitoring** and **3.14 uses wrap**~~ — **done** (`/hook_path` + `PROBE_ONLY`).
2. Parent/child **link** correctness under TaskGroup/gather (labels prove names; not link graph).
3. Staging / multi-repeat load; latency p50/p95.
4. Whether an `asyncio` **sample type** is ever expected (product schema) — still absent here.

## Harness files

- `async_app.py` — long-lived loop + churn + `/hook_path` probe
- `async_corpus.txt` — drive paths
- `run_async.sh` — venv/install/drive/ps/pprof summary (mirrors `run.sh`, `PROFILING=0` supported; `PROBE_ONLY=1` for path assert only)
- Reuses `drive.py`
