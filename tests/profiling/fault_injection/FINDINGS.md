# Fault-injection findings — profiler lifecycle suite (py3.12, run 2025-xx-xx on r1)

Run: `DD_TEST_PODMAN_COMPAT=1 scripts/run-tests --venv e7cd703 -- -k "lifecycle_faults or thread_faults or outlive_profiler" -p no:randomly`
Result: **17 failed, 14 passed, 499 deselected** (34.6s).

> Reading guide: several late failures show 3 leaked `ddtrace.profiling.scheduler:Scheduler`
> threads with *identical object addresses* — that is cross-test pollution from findings F1/F2
> below (the native `ddtrace.internal._threads.periodic_threads` registry is process-global).
> The genuinely stranded threads per run: 1 from run-path faults, 2 from stop-path faults.

---

## F1 — Run-path exception in periodic work strands the Scheduler worker thread  *(profiler bug)*

**Severity: medium-high.** An exception raised inside a scheduled flush (e.g. `memalloc.MemoryCollector.snapshot()` or `ddup.upload()`) leaves a `ddtrace.profiling.scheduler:Scheduler` `PeriodicThread` registered and alive after `Profiler.stop()` returns. `stop()` itself completes (no watchdog deadline hit) but does not join/reap the worker, and the dead/parked worker never unregisters.

- **Tests:** `test_run_fault_raises_every_flush[memalloc_collector.snapshot]`, `test_run_fault_raises_every_flush[ddup.upload]` (both: `profiler threads leaked after stop: ['ddtrace.profiling.scheduler:Scheduler']`)
- **Impact:** embedders that stop/restart the profiler leak native threads per faulted flush; the leaked registry entries also make every later leak check untrustworthy in-process.
- **Root-cause hypothesis (for investigator):** the C++ periodic worker exits on exception without unregistering, or `Scheduler._stop_service` skips `join()` when the worker status is not cleanly stopped.
- **Follow-up:** reproduce minimal case; check `_threads` unregister path on exception; decide whether `stop()` should hard-join with timeout or force-unregister dead workers.

## F2 — Stop-path exceptions strand 2+ Scheduler workers  *(profiler bug)*

**Severity: medium-high.** Exceptions during shutdown — final upload (`ddup.upload`), `Collector.stop()`, or `Scheduler.flush()` at stop — are contained (stop returns) but strand the scheduler workers. Adds 2 new leaked threads on first occurrence (`test_stop_fault_final_upload_raises` shows the set grow from 1 → 3).

- **Tests:** `test_stop_fault_final_upload_raises`, `test_stop_fault_collector_stop_raises`, `test_stop_fault_scheduler_flush_raises`, `test_stop_twice_with_faults`, `test_chaos_random_fault_mix`
- **Impact:** shutdown faults (e.g. exporter in a bad state) leave the process permanently holding profiler native threads; restart after a faulted stop leaks again.
- **Follow-up:** same as F1; additionally verify `_ProfilerInstance._stop_service`'s `if join and scheduler_stopped` guard — exceptions likely flip `scheduler_stopped` off, skipping the join.

## F3 — Hung periodic work wedges `Profiler.stop()` AND process exit, forever  *(profiler bug, confirmed in-process and subprocess)*

**Severity: high.** With `ddup.upload` (or a collector `snapshot()`) hanging, `Profiler.stop()` blocks indefinitely in the worker `join()` — confirmed in a clean subprocess: `HUNG: stop with hung upload did not complete within 5.0s`. Additionally, if `stop()` is never able to run/return, interpreter exit wedges in the native atexit join of periodic threads — a wedged profiler thereby **hangs the host application's exit with no timeout and no diagnostic**.

- **Tests (both fail by design until fixed):** `test_subprocess_hung_upload_does_not_wedge_stop`, `test_subprocess_hung_snapshot_does_not_wedge_stop`
- **Standalone repro:** `/tmp/wedge_repro.py` on r1 (hung `ddup.upload` → `stop()` never returns; child that skips its watchdog `os._exit` parks forever in atexit).
- **Follow-up:** bound `PeriodicThread.stop()/join()` with a timeout at the atexit level (at minimum), log a diagnostic when the join times out; consider making `stop()` interruptible or moving the final upload off the caller's thread.

## F4 — `stack_collector.snapshot` fault never fires  *(test/framework bug — investigation)*

The injector armed on `StackCollector.snapshot` never records a fire during a 0.8s run @ 0.2s scheduler interval (`fired == 0`), while the same harness *does* fire on `memalloc_collector.snapshot` and `ddup.upload`. Either the arm point misses the call path the scheduler actually uses for the stack collector (native worker may call it via a different symbol/bound method), or the first-fire window is longer than the run.

- **Tests:** `test_run_fault_raises_every_flush[stack_collector.snapshot]`, `test_run_fault_snapshot_hangs_temporarily`
- **Follow-up:** find the real call site/callable for stack snapshots during flush; re-arm there; keep the fired-count assertion.

## F5 — Lock-collector event integrity under fault: `acquired_time` set when acquire not sampled  *(potential profiler bug — investigation)*

The workload containment oracle caught lock events whose `lock_events`-invariant is violated while the scheduler is faulted: `Expected acquired_time to be None when acquire is not sampled, got 15356971754007720`.

- **Test:** `test_run_fault_scheduler_thread_dies`
- **Impact (if real):** lock-time metrics misattribute acquire latency when lock profiling is enabled and the profiler machinery is degraded — a data-integrity issue, not just liveness.
- **Follow-up:** reproduce in isolation (lock collector + kill scheduler worker); determine whether the unsampled-acquire path skips `acquired_time` clearing under error paths, or whether the oracle's invariant is too strict (cross-check `tests/profiling/collector/test_lock.py` semantics).

## F6 — Telemetry dependency-collector lifecycle vs profiler  *(cross-product behavior — refine test)*

The deliberate telemetry pin ("dependency collector outlives `Profiler.stop()`") did **not** hold in this run: no telemetry threads remained after a short start/stop, while in earlier runs the `_TelemetryDependencyCollector` thread lingered after every profiler stop. The telemetry writer thread's lifetime evidently depends on its own flush/idle cadence (~30s) and activation bookkeeping — not simply on profiler stop. The per-product oracle split is in place; the pin test needs to observe telemetry state *at activation time* rather than after a sub-second stop, or accept both outcomes with a documented explanation.

- **Test:** `test_telemetry_threads_outlive_profiler`
- **Follow-up:** inspect `ddtrace/internal/telemetry/writer.py` `_TelemetryDependencyCollector` start/stop conditions (`product_activated`, first-flush timing); rewrite the pin as behavior documentation, not a hard assert.

## F7 — Multi-threaded `fork()` DeprecationWarning fails the subprocess stderr check  *(test bug, trivial)*

The fork test's child prints Python 3.12's `DeprecationWarning: This process is multi-threaded, use of fork() may lead to deadlocks in the child` on stderr; the marker helper requires empty stderr.

- **Test:** `test_fork_while_running`
- **Fix:** suppress the warning in the child (`warnings.filterwarnings`) or pass `expected_err` matching it.

## F8 — Cross-test leak pollution (test isolation)  *(framework improvement)*

Leaked threads from F1/F2 persist in the process-global registry and make every subsequent leak assertion report the same 3 stale threads (identical addresses across `chaos`, `hammer`, `race`, `churn` tests — none of which demonstrably added new leaks themselves, but they cannot be judged fairly until isolated).

- **Follow-up:** snapshot the native registry at test start and assert on the *diff*; or run leak-asserting tests via the subprocess marker; or add a fixture that force-reaps/ignores pre-existing registry entries.

---

### Suggested ticket split

| Ticket | Finding | Component | Type |
|---|---|---|---|
| 1 | F3 | profiling (PeriodicThread/join/atexit) | bug, high |
| 2 | F1+F2 | profiling (scheduler stop/exception handling) | bug, med-high |
| 3 | F5 | profiling (lock collector data integrity) | investigation |
| 4 | F4 | tests/profiling/fault_injection (arm point) | test bug |
| 5 | F6 | telemetry lifecycle vs product activation | doc/behavior |
| 6 | F7+F8 | fault_injection framework | test hygiene |
