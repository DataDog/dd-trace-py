# Profiler fault-injection framework

While the existing profiler tests fuzz *memory reads* (memalloc snapshot
parsing, pprof round-trips), nothing systematically makes the profiler's
**lifecycle** fail. This framework does.

## The fault model

The profiler pipeline (`ddtrace/profiling/profiler.py`):

```
Profiler.start()
 └─ _start_exporter()        ddup.config(), ddup.start()                ┐
 └─ _start_collectors()      Collector.start()  (one worker thread each) ├─ START
 └─ Scheduler.start()        upload worker thread                       ┘
 run                         snapshot() on flush, ddup.upload()          ── RUN
Profiler.stop()
 └─ Scheduler.stop/join/flush                                             ┐
 └─ Collector.stop()/join()   (per collector)                             ├─ STOP
                                                                            ┘
```

Every box is a fault point. The injector (`faultlib.py`) can make any of them:

| action     | effect                                                        |
|------------|---------------------------------------------------------------|
| `raise_()` | raise an exception at the call site (any type, incl. OOM)      |
| `hang()`   | block the calling thread (bounded, or forever)                |
| `skip()`   | silently skip the original call and return a canned value      |
| `kill()`   | `os._exit` — fatal, for subprocess scenarios only               |

with policies `always` / `once` / `nth` / `probability` / arbitrary predicate
on the call context (args, thread, hit count).

## The oracle

Under *any* single fault, these must hold (else the profiler has failed the
host application):

* **Liveness** — `start()`/`stop()` return within a deadline; no lifecycle
  call may wedge the application thread (`faultlib.completes_within` dumps
  every thread's stack on violation).
* **Containment** — faults never leak exceptions into application threads
  (`faultlib.Workload` runs CPU/alloc/lock-churn threads under the profiler
  and records anything that escapes).
* **Cleanliness** — after `stop()`, no profiler worker threads remain
  (checked via `threading.enumerate()` *and* the native
  `ddtrace.internal._threads.periodic_threads` registry, because profiler
  workers are C-level threads, not `threading.Thread`), and a fresh profiler
  can still start (restartability).

## Suites

* `test_lifecycle_faults.py` — start/run/stop fault matrix + hang scenarios
  (in subprocesses: a hung `stop()` parks native threads that also block
  process exit) + seeded chaos mixes.
* `test_thread_faults.py` — concurrent start/stop hammering, two-profiler
  start race, thread churn under sampling, `fork()` while running, SIGTERM
  racing the lifecycle.

## Known sharp edges the framework targets (from code reading)

1. `_ProfilerInstance._start_service` starts collectors, then the scheduler.
   A scheduler start failure raises out of `Profiler.start()` with collectors
   left running → leaked worker threads (`test_start_fault_scheduler_thread_fails`).
2. `Scheduler.flush` runs `before_flush` (snapshots) then `ddup.upload` on the
   worker thread; `_ProfilerInstance._stop_service` joins the scheduler with
   **no timeout**, so a hung upload/snapshot wedges `Profiler.stop()` — and the
   C-level atexit join wedges process exit
   (`test_subprocess_hung_upload_does_not_wedge_stop`,
   `test_subprocess_hung_snapshot_does_not_wedge_stop`).
3. An exception in `Scheduler.periodic` kills the upload worker thread
   silently (`test_run_fault_scheduler_thread_dies`).

## Running

```
riot -v 3.12 -s profiling-fault-injection --pass-env "PYTEST_ADDOPTS" run -k fault_injection
# or, with an already-importable ddtrace checkout:
python -m pytest tests/profiling/fault_injection/ -x -v
```

## What we ran and what we found (summary)

Full run of the suite in the sanctioned containerized flow (py3.12): 31 tests,
17 failures — classified into 8 findings (F1–F8). Post-hoc verification (one
standalone reproducer per finding in `repro/`, isolated reruns of the failing
tests, full-suite rerun) then reclassified them:

- **Genuine profiler bug (high, confirmed)** — **F3**: a hang in periodic work
  (e.g. stuck exporter upload) wedges `Profiler.stop()` indefinitely and also
  hangs host-application exit via the native atexit join — no timeout, no
  diagnostic.
- **Open profiler question** — **F5**: lock-collector invariant violation
  (`acquired_time` set when the acquire was not sampled) surfacing inside
  application threads while the machinery is degraded.
- **Everything else reclassified** — the run/stop-path "thread leaks" (F1/F2)
  and the never-firing stack arm (F4) are cross-test pollution: tests failing
  before their stop abandon running profilers in the process-global registry
  (F8, the framework's own isolation gap). The profiler's exception containment
  held up everywhere we could reproduce it in isolation — its weakness is
  hangs. F6 (telemetry lifecycle) and F7 (fork DeprecationWarning) are test/doc
  fixes.

Full details, evidence, and fix directions: `FINDINGS.md` (executive summary
at the top) and `repro/INVESTIGATION.md`.
