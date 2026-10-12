# Fault-injection findings — investigation state & reproducers

**Branch:** `profiler-fault-injection` · **Suite:** `tests/profiling/fault_injection/`
**First containerized run (py3.12, venv `e7cd703`):** 17 failed / 14 passed / 499 deselected.
Full classification lives in `../FINDINGS.md`; this folder adds one **standalone
reproducer per finding** so an investigator can verify each without pytest.

## Running the repros

On r1 (`workspace-fullhost-r1`), repo at `~/dd/dd-trace-py-fi`:

```bash
cd ~/dd/dd-trace-py-fi/tests/profiling/fault_injection/repro
./run_all.sh                                   # all findings, timeout-guarded
~/dd/dd-trace-py-fi/.venv-fi/bin/python repro_f3_stop_wedge.py   # a single one
```

Exit codes: `0` not reproduced · `1` **reproduced** · `3` stop()-itself-wedged
guard (F3 family) · `124` killed by timeout.

Environment notes:
- Repros default `FI_REPO` to `~/dd/dd-trace-py-fi` (override with `FI_REPO=...`).
- The `.venv-fi` python is the quick path (editable ddtrace with native ext built).
  The sanctioned flow for the *suite* remains
  `DD_TEST_PODMAN_COMPAT=1 scripts/run-tests --venv e7cd703 -- -k "lifecycle_faults or thread_faults" -p no:randomly`.
- F1/F2/F3 findings were observed inside the sanctioned container run; the
  standalone repros run on the host venv against the same ddtrace build.
- Repros avoid normal interpreter exit whenever a wedged/dead worker could park
  the atexit join (`os._exit`) — see F3(b) for the deliberate exception.

---

## F1 — run-path exception strands the Scheduler worker  · *status: RECLASSIFIED — isolated runs PASS; full-run leak was cross-test pollution (see F8)*

- **Repro:** `repro_f1_run_path_exception_leak.py` (`--point memalloc` default, `--point upload` alternative)
- **Empirical status:**
  - Isolated container run (`-k "test_run_fault_raises_every_flush or test_stop_fault_final_upload_raises or test_stop_fault_collector_stop_raises"`): **all 5 tests PASS**.
  - Standalone repro on both fault points: `exit 0` — exception contained, worker reaped, registry clean.
  - Full-suite run: deterministic FAIL with `profiler threads leaked after stop: ['ddtrace.profiling.scheduler:Scheduler']`.
- **Explanation:** in the full run, an earlier test that fails BEFORE calling `assert_clean_shutdown` (e.g. `[stack_collector.snapshot]` failing at the fired-count assert) abandons its RUNNING profiler — pytest teardown (`_reset_profiler_state`) only clears globals, never stops the profiler — so its scheduler thread pollutes every later leak assertion (identical object addresses across failures).
- **Investigator tasks:** (1) confirm the pollution chain by making `_reset_profiler_state` stop any active instance; (2) re-run the full suite and re-baseline; (3) if a genuine leak survives isolation, re-open this finding.

## F2 — stop-path exception strands 2+ Scheduler workers  · *status: RECLASSIFIED — same pollution explanation as F1*

- **Repro:** `repro_f2_stop_path_exception_leak.py` (faulted stop + restart cycle, mirroring the suite flow incl. `assert_restartable`)
- **Empirical status:** isolated container run **PASSES** (`test_stop_fault_final_upload_raises`, `test_stop_fault_collector_stop_raises`); standalone repro incl. restart cycle: `exit 0` (fault fires during both stop windows, no leaks). Full run: deterministic FAIL reporting 3 stale threads — the stale set accumulated from earlier failures.
- **Investigator tasks:** same as F1 (fixture stop + re-baseline) before spending time in `scheduler.py`.

## F3 — hung periodic work wedges `stop()` and process exit  · *status: confirmed standalone AND in suite; xfailed in suite*

- **Repro:** `repro_f3_stop_wedge.py` (two children: stop-wedge, atexit-wedge)
- **Symptom (suite):** `test_subprocess_hung_upload_does_not_wedge_stop` /
  `test_subprocess_hung_snapshot_does_not_wedge_stop` print
  `HUNG: stop with hung upload did not complete within 5.0s` (both now xfail-marked).
- **Mechanism:** `ddup.upload` (or a snapshot) parks forever inside the scheduler
  worker's periodic call; `Scheduler.stop()` → worker `join()` blocks on
  `stopped_event`, which the parked worker never sets. If the app never regains
  control, interpreter exit parks in the native atexit join of periodic threads
  (no timeout, no diagnostic) → host application hangs.
- **Look at:** `ddtrace/internal/_threads/` C++ (PeriodicThread::stop/join, the
  atexit handler), `ddtrace/internal/atexit.py` (note: `DD_TESTING_RAISE` behavior).
- **Fix directions:** bound the join with a timeout (log a diagnostic, then abandon
  or detach the worker) at minimum in the atexit path; consider making the final
  upload interruptible (deadline/abortable IO) or moving it off the caller's thread.
- **Verify:** repro (a) child exits 0 (stop returned); repro (b) child exits promptly;
  un-xfail the two suite tests and they pass.

## F4 — `StackCollector.snapshot` class-level patch never fires  · *status: order-dependent test-harness bug — fires fine in a fresh process*

- **Repro:** `repro_f4_stack_snapshot_arm_miss.py` (counts class-attr snapshot calls
  vs ddup.upload control; dumps per-collector `snapshot` binding diagnostics)
- **Empirical status:**
  - Isolated container run: `test_run_fault_raises_every_flush[stack_collector.snapshot]` **PASSES** (fault fires).
  - Standalone: plain class-attr counting patch fires (2 calls) — the call path IS the Python class attribute.
  - Full-suite run: deterministic `fired == 0`.
- **Conclusion so far:** the injector's arm (staticmethod-safe wrapping in `faultlib.Injector`) or
  leftover patched state from the first ~13 (passing) tests suppresses the arm. Deterministic,
  so bisectable: run the suite with progressively fewer preceding tests to find the polluting one.
- **Fix:** fix the arm/disarm in `faultlib.py` (or the polluting test's cleanup), keep the fired-count assertion.

## F5 — lock-collector invariant leaks into application threads  · *status: observed in suite only; standalone repro is best-effort*

- **Repro:** `repro_f5_lock_event_integrity.py` (kills `Scheduler.periodic` worker
  while lock-churning with `DD_TESTING_RAISE=1` + `DD_PROFILING_ENABLE_ASSERTS=1`)
- **Symptom (suite):** workload threads die with
  `AssertionError('Expected acquired_time to be None when acquire is not sampled, got 15356971754007720')`
  — this is `ddtrace/profiling/collector/_lock.pyx:218`, gated by
  `DD_PROFILING_ENABLE_ASSERTS`. A sampled acquire sets `acquired_time`
  (`_lock.pyx:239`), the release clears it (`_lock.pyx:269`); the assert fires when
  a later **unsampled** acquire sees a stale `acquired_time` from the same
  profiling generation → a release path cleared-state miss.
- **Open questions:** (1) Is this a real state bug (stale `acquired_time`
  producing a bogus release sample with wrong durations) or is the assert itself
  too strict across edge cases (e.g. generation checks, exception paths between
  acquire and release)? (2) Why does killing the scheduler worker trigger it —
  interaction via generation/restart bookkeeping or sampling cadence?
- **Authoritative repro:** pytest `test_run_fault_scheduler_thread_dies`
  (interaction-dependent; the standalone may need tuning — if it exits 0, run the
  pytest test in the container instead before concluding).
- **Fix directions:** ensure `acquired_time` is cleared on every release path;
  or reset per-lock state on collector generation change (`_lock.pyx:206-247`).
- **Verify:** assert no longer trips under the fault; check
  `tests/profiling/collector/test_lock.py` semantics for the invariant.

## F6 — telemetry lifecycle around profiler start/stop  · *status: observation complete — telemetry thread is an import-time global*

- **Repro:** `repro_f6_telemetry_lifecycle.py` (timeline; always exits 0)
- **Observation (standalone, fresh process):** the `_TelemetryDependencyCollector`
  thread is running **before any `Profiler.start()`** (it starts at ddtrace import)
  and remains running for the entire 35s observation window after `Profiler.stop()`.
- **Conclusion:** telemetry lifecycle is process-global and independent of profiler
  activation/stop; the suite pin test's assumption ("starts on profiler activation,
  outlives stop") is wrong. Rewrite `test_telemetry_threads_outlive_profiler` as
  behavior documentation; the per-product oracle split already records telemetry
  leftovers without failing profiler tests.

## F7 — fork() DeprecationWarning breaks subprocess stderr check  · *status: trivial test bug*

- **Repro:** `repro_f7_fork_stderr.py`
- **Symptom (suite):** `test_fork_while_running` child prints Python 3.12's
  multi-threaded-fork DeprecationWarning; the marker's stderr assertion expects empty.
- **Fix:** in the child, `warnings.filterwarnings("ignore", category=DeprecationWarning)`
  around the fork (or pass an expected-stderr matcher); keep the fork deadlock
  semantics test itself.

## F8 — cross-test pollution / missing isolation  · *status: explains F1/F2 (and F4's order dependence) — now the top framework work item*

- **Repro:** `repro_f8_registry_pollution.py` (faulted cycle then clean cycle; on a
  clean process both cycles leave nothing — consistent with F1 reclassification)
- **Mechanism (from the full-run analysis):**
  1. A test that fails BEFORE `assert_clean_shutdown` (e.g. `[stack]` at the fired-count
     assert) abandons its RUNNING profiler — `_reset_profiler_state` clears globals but
     never stops the instance. Its scheduler worker stays in the process-global
     registry and later leak assertions fail on it (identical addresses).
  2. Leaked/patched state from earlier (passing) tests suppresses later arms (F4) —
     deterministic, hence bisectable.
- **Fixes:**
  1. `conftest.py`: teardown should stop any active profiler (`Profiler._active_instance`)
     and disarm leftover injectors — hard-kill via watchdog to survive wedges.
  2. `faultlib`: leak oracle should snapshot `ddtrace.internal._threads.periodic_threads`
     at test start and assert on the **diff**.
  3. Consider the subprocess marker for leak-asserting tests.
- **Verify:** full-suite run is green apart from genuinely-failing findings; failures
  no longer reference threads created by earlier tests.

---

## Suggested investigation order

1. **F8** (test-isolation fixes: stop abandoned profilers in teardown, registry-diff oracle)
   — it explains the F1/F2/F4 full-run failures; fix it first and re-baseline.
2. **F3** (worst: host-app hang, standalone-confirmed twice) — fix direction known.
3. **F5** (data-integrity question; needs the pytest interaction repro).
4. **F4** (bisect the deterministic order-dependence once F8's teardown fix lands).
5. F7 (trivial stderr fix), F6 (rewrite pin test as documentation).

## Post-hoc verification results (isolated reruns + standalone repros)

- Isolated container run of the 5 leak/fire tests (`-k "test_run_fault_raises_every_flush or test_stop_fault_final_upload_raises or test_stop_fault_collector_stop_raises"`): **all 5 PASS**.
- Full-suite reruns: deterministic (15 failed + 2 xfailed, same set every run).
- Standalone repros on r1: F3 **REPRODUCED** (stop-wedge + atexit-wedge), F7 **REPRODUCED**,
  F1/F2 contained (`exit 0`), F4 plain class-attr patch fires, F6 telemetry thread is an
  import-time global.
- Consequence: F1/F2 are reclassified as pollution artifacts (see F8); F4 is order-dependent
  harness state; the only profiler bugs standing are **F3** (confirmed) and **F5** (open).
