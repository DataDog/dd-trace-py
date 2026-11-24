# Native GC pause tracker

Status: design.
Only the shared header `ddtrace/internal/runtime/include/gc_callbacks.hpp` exists, and no build includes it yet.

`ddtrace.internal.runtime._gc_pause` is a small C++ extension that measures CPython garbage collection pauses from a native `gc.callbacks` entry.
The GC Time collector (`ddtrace/profiling/collector/gc.py`) is its first user.
It replaces that collector's Python pause path: the `GCPauseMonitor` listener (`ddtrace/internal/runtime/gc_monitor.py`) and `GCCollector._on_pause`.

Short native paths such as `dd_wrapper/...` and `stack/...` are relative to `ddtrace/internal/datadog/profiling/`, as in [Architecture](Architecture.md).

## Why native

- **The signal gap.**
  `GCPauseMonitor._on_gc` takes its lock without blocking, then enters `try`.
  A Python signal handler that raises (a `SIGALRM` timeout, `KeyboardInterrupt`, `SystemExit` from a `SIGTERM` handler) can run between the two, and the lock then stays held.
  The comment above that `try` says Python code cannot close this gap, and a stress probe reproduced the leak on 3.9 and 3.15.
  A native callback runs no bytecode while it holds its lock, so no Python signal handler runs there, and a customer's signal handler can never raise inside it.
- **No Python code per collection.**
  In a micro-benchmark on 3.9 and 3.15, the collector's per-pause listener cost 1.5 to 2.2 µs, and the forksafe lock pair alone cost 1.3 to 1.4 µs, against 55 to 94 ns for a raw lock.
  A plain lock removes most of that difference, so the native path has to beat the plain-lock Python path, not today's.
- **Free-threaded builds.**
  There, CPython runs `gc.callbacks` outside the stop-the-world phase (`gc_collect_main` in `Python/gc_free_threading.c`), so the callback and a flush really run in parallel.
  The module declares `Py_mod_gil = Py_MOD_GIL_NOT_USED`, and its shared state is atomics or behind a try-lock.

## Port boundary

- C++: the callback, both clocks, thread IDs, the windows with their event cap, the try-locks, install and uninstall, and the fork reset.
- Python: configuration, collector lifecycle, the install reference count and its lock, the `gc.collect` wrapper and its explicit-call count, thread names at flush, ddup emission, debug logs, and the `forksafe` registration.

## Home and build

- `ddtrace/internal/runtime/_gc_pause.cpp`, built like `ddtrace/internal/_threads.cpp`: a plain `Extension` in `setup.py`, on every platform, with `-std=c++20` (`/std:c++20 /MT` on Windows), plus a `.pyi` stub.
- Not `_stack`, which builds only on 64-bit Linux and macOS with Python below 3.16, links `libdd_wrapper`, and reads CPython internals.
  Pause timing needs only CPython's public C API, so `_gc_pause` has no per-version headers and no `Py_BUILD_CORE`.
  It builds on a superset of the platforms where `ddup` builds, so the collector needs no fallback for a missing module.
- `scripts/import-analysis/layers.json` classifies `ddtrace.internal.runtime` as `product:runtime`.
  Classify `ddtrace.internal.runtime._gc_pause` and its Python wrapper as `internal-core`, like `gc_monitor`.
- `.github/CODEOWNERS` gives `ddtrace/internal/runtime/` to apm-sdk-capabilities.
  Add profiling-python as co-owner of the new files, as for `gc_monitor.py`.
- One process-global state, never freed (as `GCFrameTracker::get()` does, for uWSGI exits).
  `start()` raises outside the main interpreter.
  `Py_mod_multiple_interpreters` stays unset, as in `_threads`.

Two `setup.py` details decide where headers go:

- `ExtensionHashes` hashes a plain `Extension` by its `sources` and every `.pxd` file, not by its headers.
  Give `_gc_pause` `depends=[...]` and hash `ext.depends` too, or a header-only change restores a stale cached `.so`.
- A `CMakeExtension` hashes every file under `source_dir` and `extra_source_dirs`, and `_stack` lists only its own directory, `cmake/` and `dd_wrapper/`.
  Add `ddtrace/internal/runtime/include/` to `_stack`'s `extra_source_dirs` when `gc_frame_tracker.cpp` includes the shared header.

So the shared header sits alone in `include/`, and edits to `_gc_pause.cpp` or the pause core do not invalidate `_stack`.

## Python API

Consumers use `ddtrace/internal/runtime/gc_pause.py`, a thin wrapper over the extension:

```python
def start(threshold_ns: Optional[int]) -> None: ...
def stop() -> None: ...
def drain() -> Optional[Window]: ...
def _state() -> dict[str, int]: ...  # tests and debug logs
```

- The wrapper keeps the reference count and serializes `start()` and `stop()` with its own lock.
  Neither the callback nor `drain()` takes that lock, so nothing that runs during a collection can leave it held.
  Installing can release the GIL (importing `gc`, or a collection while appending to `gc.callbacks`), so a native mutex there would have to release the GIL to wait, with the finalization checks that requires.
- `start(threshold_ns)` adds a reference; the first one installs the callback and starts an empty window.
  Pauses of at least `threshold_ns` become events; `None` keeps no events.
  The last caller's threshold wins.
  On failure it raises `RuntimeError` and leaves the count unchanged; `Profiler._start_collectors` already disables a collector whose start raises.
- `stop()` drops a reference and does nothing at zero.
  The last one removes the callback and clears in-flight start times, so a later start cannot pair a new stop with an old start.
- `drain()` returns the current window and starts a new one.
  It returns `None` while another `drain()` runs, and the data waits for the next call.
- At import, the wrapper registers the extension's `reset_after_fork()` with `forksafe` (see [Fork](#fork)), so no consumer has to.

`Window` is a named tuple:

- `pause_count` and `pause_total_ns`, three each: pauses below the threshold, plus pauses that missed the event cap. Events are not in these totals.
- `events`: `(generation, pause_ns, end_monotonic_ns, thread_id, native_thread_id)` tuples.
- `dropped_pauses` and `dropped_pause_ns`: pauses at or above the threshold that missed the event cap. They are also in the totals.
- `skipped`: stops lost to lock contention or without a start. In no total; debug logs only.

These are the values `GCCollector.snapshot()` reads today, so the samples, frames (`gc.collect[gen=N]` and `gc.config`, file `gc`) and labels do not change.

The reference count shares the installed callback, not the window.
A second consumer that drains, such as runtime metrics, needs its own window, updated by the same callback.

## Native design

### Callback

- A `METH_FASTCALL` `PyCFunction` named `_ddtrace_gc_pause_callback`, distinct from the stack sampler's `_ddtrace_gc_callback`, so tests can tell them apart (`tests/profiling/collector/gc_utils.py`).
- It never returns `NULL` and never leaves an exception set, because CPython reports a failing callback as unraisable (`invoke_gc_callback`).
  Bad arguments, a non-dict `info` and a missing or out-of-range generation are ignored.
- `noexcept`, and nothing in it can throw.
- No allocation.
  The generation lookup uses an interned key built at module init, with `PyDict_GetItemWithError` (`PyDict_GetItemRef` on 3.13+).
  `PyDict_GetItemString` would build a temporary key string on every call (`Objects/dictobject.c`).
- `start` phase: when the tracker is not installed (an atomic flag), return.
  Otherwise store the pause clock in the generation's start slot, an atomic.
- `stop` phase: exchange the start slot with 0; with no start, count `skipped`.
  Compute the pause, then try the buffer lock once:
  - busy: count `skipped`;
  - at or above the threshold with a free event slot: write an event, and only then read the timeline clock and both thread IDs;
  - otherwise: add the pause to its generation's totals, and to the dropped counters if it was at or above the threshold.
- Event cap: 1,024 per window, and the last 64 slots only take pauses of 10 ms or more, as in `gc.py` today.

CPython runs one collection at a time per interpreter (a compare-and-swap on `collecting` in 3.14+ and free-threaded builds), so one thread writes the start slots at a time.
Generations are what CPython reports, 0 to 2.
On 3.14, whose incremental collector reports every automatic collection as generation 1, `gc.collect[gen=1]` counts those increments.

### Clocks

- **Pause:** `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` on POSIX, the clock behind `time.thread_time_ns` there (`Modules/timemodule.c`).
  On Windows, a monotonic clock, because thread CPU time only advances once per scheduler tick (the policy in `gc_monitor.py`).
  A pause stays the collecting thread's CPU time.
- **Event end:** the clock of `Datadog::get_monotonic_ns()` (`dd_wrapper/include/clock.hpp`): `CLOCK_MONOTONIC` on Linux, `mach_absolute_time` on macOS.
  `push_monotonic_ns` adds a fixed offset computed from that clock (`dd_wrapper/src/sample.cpp`), so another clock, including `clock_gettime(CLOCK_MONOTONIC)` on macOS, misplaces events.
  `_gc_pause` does not link `dd_wrapper`, which only builds where `ddup` does, so the core header copies that function with a Windows branch, and a test checks event end times against `time.monotonic_ns()`.
- CPython 3.15 adds `duration` to the callback's `info`.
  It is wall time measured inside the collection (`PyTime_PerfCounterRaw`), and older versions lack it, so the tracker does not use it.

### Thread IDs

`PyThread_get_thread_ident()` and `PyThread_get_thread_native_id()`, the functions behind `_thread.get_ident()` and `_thread.get_native_id()`.
The native ID is `gettid` on Linux, `pthread_threadid_np` on macOS and `GetCurrentThreadId` on Windows (`Python/thread_pthread.h`, `Python/thread_nt.h`).
gevent replaces the Python-level functions, not these.
Neither call allocates or releases the GIL.

### Locking

- Two windows and an active index (a double buffer).
- Buffer lock: a try-lock on a `std::atomic<bool>`.
  The callback tries it once and never waits.
  `drain()` swaps the active index under it, and install empties the active window under it, both with a short spin.
  Nobody runs Python code or allocates while holding it.
- Drain lock: a second try-lock, held by `drain()` from the swap until it has built its result and cleared the drained window.
  A nested `drain()` (from a finalizer that a collection runs during the build) returns `None`.
- Installed flag and threshold: atomics, read by the callback without a lock.

`drain()` builds Python objects only after it releases the buffer lock, so a collection during the build records into the new active window.

FS-5 in `docs/contributing-architecture.rst` asks for reentrant locks wherever GC callbacks take a lock.
A lock the callback only tries meets the same goal: same-thread reentry fails the try, and nothing waits.

An atomic try-lock rather than `std::mutex`: every user only tries it or spins briefly, none of its operations can throw, and resetting it in a fork child is a plain store, with no `pthread_mutex_t` to recreate.

### Fork

- `gc_pause.py` registers a generator hook with `forksafe.register`.
  Generator hooks run before the regular after-in-child hooks (`run_child_hooks` in `ddtrace/internal/forksafe.py`), so the reset precedes any hook that could record a pause.
  Precedent: `_reset_provider_locks` in `ddtrace/internal/openfeature/_provider.py`.
- `reset_after_fork()` stores `false` into both try-locks (their holders do not exist in the child), clears the start slots and both windows, and keeps the installed callback, which the child's `gc.callbacks` still holds.
  The wrapper's lock is a `forksafe.Lock()`, and its reference count carries over with the callback.
- A collection in the child before the reset fails the try-lock and counts `skipped`; it cannot hang.
- `forksafe` and not `pthread_atfork`, unlike the profiler's native libraries: FS-1 asks for `forksafe`, and the module will be shared with runtime metrics.
  The try-lock makes the time between `fork()` and the hook harmless.

### Error paths

No C++ exception crosses into CPython. Each path and its injection test:

- Callback arguments (wrong count, non-str phase, non-dict `info`, generation missing, not an int, negative or overflowing): ignored, no exception left set.
  Test: call the installed callback from `gc.callbacks` with each.
- `start()` when `gc.callbacks` is missing or not a list, or the append fails: raises, and the count is unchanged.
  Test: patch `gc.callbacks` with a tuple.
- `stop()` when the removal fails: the count stays at zero, and the next `start()` finds the callback already registered.
  Test: the same patch.
- `drain()` when building the result fails: releases both locks, discards the drained window and raises `MemoryError`.
  Test: `_testcapi.set_nomemory`, where available.
- Lock contention in the callback: counts `skipped`.
  Test: core unit test.
- `start()` outside the main interpreter: raises.
  Test: `_testcapi.run_in_subinterp`, where available.

## Python side

`gc.py` keeps configuration, lifecycle, the `gc.collect` wrapper with its own lock, thread names at flush, and emission.
It drops `_on_pause`, the monitor listener and the preallocated event lists.
`_start_service` calls `gc_pause.start(threshold_ns)`, `_stop_service` calls `gc_pause.stop()`, and `snapshot()` emits from `gc_pause.drain()`.

Runtime metrics keep `GCPauseMonitor`.
Moving them onto `_gc_pause` is a separate change, and it needs the second window described above.

## Configuration

The native module reads no environment variable.

- `DD_PROFILING_GC_TIME_ENABLED` (formerly `DD_PROFILING_GC_ENABLED`) turns the GC Time collector on, and only the collector's start installs the callback.
  It is off by default, which also makes it the off switch for the native path.
  No setting chooses between native and Python pause tracking.
- `DD_PROFILING_GC_TIME_PAUSE_EVENT_THRESHOLD_MS` (formerly `DD_PROFILING_GC_PAUSE_EVENT_THRESHOLD_MS`): float milliseconds, default 1.0, valid from 0 to infinity (`ProfilingConfigGC` in `ddtrace/internal/settings/profiling.py`).
  `gc.py` passes it to `start()` in nanoseconds, and infinity as `None`.
  Today `GCCollector.__init__` computes `int(threshold_ms * 1_000_000)`, which raises `OverflowError` for infinity.
- `DD_PROFILING_STACK_GC_ENABLED` is separate: it puts "Garbage collection" frames on stack samples through `GCFrameTracker`.
  With both on, each collection runs two native callbacks.

## Shared gc.callbacks helpers

`ddtrace/internal/runtime/include/gc_callbacks.hpp` is header-only, uses only CPython's public C API, needs no `Py_BUILD_CORE`, and builds on 3.9 to 3.15, free-threaded builds included.

- `parse_phase(phase)`
- `new_generation_key()` and `parse_generation(info, key)`
- `get_list()`
- `append_once(callbacks, callback)`: identity scan, then append, in the list's critical section.
- `remove_all(callbacks, callback)`: removes every entry that is `callback` itself.

They come from `stack/src/gc_frame_tracker.cpp`, where they only build before 3.15, because from 3.15 the tracker reads `interp->gc.frame` and installs no callback.
`parse_generation` is new, since `GCFrameTracker` ignores `info`.

`GCFrameTracker` can adopt the header without behavior change: `gc_callback`'s phase checks become `parse_phase`, `get_gc_callbacks()` becomes `get_list()`, the install's scan and append become `append_once`, and the uninstall's loop becomes `remove_all`.
It keeps its per-interpreter slots, frame capture and fork handlers.
The header compiles in `gc_frame_tracker.cpp`'s include order (`Py_BUILD_CORE`, then `internal/pycore_interp.h` on 3.15).
The leaked singleton stays a one-line idiom in each user, because a template helper could not call `GCFrameTracker`'s private constructor.

On 3.13 free-threaded builds, `append_once` is not atomic.
`PyList_Append` enters the list's critical section again, and before 3.14 that nested entry blocks on the mutex the outer section holds, which detaches the thread and suspends the outer section.
`gc_pause.py` serializes installs with its lock.
`GCFrameTracker` releases its mutex before the scan, so on 3.13t two concurrent installs could register its callback twice.

## Tests

The lists below reflect `tests/profiling/collector/test_gc.py` on this branch.
Sort tests added later, such as those for the plain-lock and bounded-wait changes, the same way.

Unchanged:
`test_stop_preserves_later_gc_collect_wrapper`, `test_explicit_collects_counted_on_config_sample`, `test_patch_active_set_before_collect_replacement`, `test_gc_pause_samples_appear_in_profile`, `test_gc_flag_keeps_profiler_positional_parameter_order`, `test_default_gc_collector_not_installed`, `test_patched_collect_forwards_the_caller_arguments`, and, apart from the rename, `test_enable_gc_collector_via_env` and `test_pause_event_threshold_from_config`.
`tests/tracer/runtime/test_gc_monitor.py` is not affected.

pprof-level tests that read Python internals, ported with the same assertions on the profile:

- `test_gc_pause_labels_in_profile` calls `_on_pause` to pass the cap.
  Use threshold 0 and real `gc.collect(0)` calls; the dropped-time bound becomes "greater than 0".
- `test_gc_exported_counts_and_durations` reads `_pause_count` and `_pause_total_ns`.
  Read them from `_state()` before `snapshot()`.

Ported to the new API:

- `test_start_installs_and_stop_removes_hooks`: exactly one `_ddtrace_gc_pause_callback` while started, none after stop, still one after a restart, and the collector installs no monitor hook.
- `test_reset_after_fork_clears_in_flight_state`: real collections, `reset_after_fork()`, then `_state()` is zero.
- `test_rollback_after_failed_first_start_restores_gc_collect` and `test_rollback_after_failed_start_keeps_other_monitor_reference`: patch `gc_pause.start`; another holder's reference survives the rollback.
- `test_real_fork_resets_gc_state_in_the_child_only`: plant a start through the installed callback (`cb("start", {"generation": 2})`), fork, and compare `_state()` in both processes.
  The core tests cover a lock held at fork time.
- `test_pause_event_keeps_native_id_after_thread_exits` and `test_pause_event_thread_id_ignores_later_get_ident_patch`: a worker thread calls `gc.collect()` with threshold 0.
- `test_overlapping_snapshots_emit_each_pause_once`: a real collection and a nested `snapshot()` during the first snapshot's emission.

Emission tests on a patched `gc_pause.drain` that returns a fixed `Window`, ddup assertions unchanged:
`test_on_pause_records_pause_gctime`, `test_snapshot_gctime_equals_total_pause_ns`, `test_long_pause_emitted_as_timestamped_event` and `test_dropped_pauses_counted_on_config_sample_per_window`.

Moved to the core tests, because they need exact durations, a smaller cap or an injected clock:
`test_lock_held_skips_instead_of_waiting`, `test_pause_events_past_cap_roll_into_aggregates`, `test_pause_events_capped_at_1024`, `test_last_pause_event_slots_only_take_long_pauses`, `test_on_pause_does_not_grow_event_buffers` and `test_on_pause_reads_clock_and_thread_ids_only_for_kept_events`.
For real pauses of 10 ms or more in Python tests, use `busy_cyclic_collection`: `slow_cyclic_collection` sleeps in `__del__`, which uses no thread CPU time.

Core tests: `_gc_pause_core.hpp` holds the Python-free logic (windows, cap, reserve, try-locks, drain swap, fork reset).
GoogleTest cases in `stack/test/` run in the `profiling_native` job under ASan/UBSan, TSan and Valgrind on 3.9 to 3.15 ([Testing](Testing.md)).
Cases: cap and reserve, dropped counters, drain swap, contention, a real `fork()` while another thread holds the lock, and the callback and `drain()` racing under TSan.
`scripts/check_profiling_native_coverage.py` only scans the profiling directories, so add the `_gc_pause` sources and headers to the `profiling_native` `rules:changes` and to that script.

Platforms: Python tests run on Linux and macOS, and `tests/smoke_test.py`, which cibuildwheel runs for every wheel (Windows included), imports `_gc_pause`.

Benchmark: per-collection cost of the callback pair, native against the plain-lock Python path, on 3.9 and 3.15.

## Rollout

The Python pause path stays the shipping path, and the fallback, until the native path passes all of these:

1. The pprof-level GC tests pass on 3.9 to 3.15, unchanged except for the two ports above.
2. The core tests pass under ASan/UBSan, TSan and Valgrind, and `_gc_pause` builds and imports in the Linux, macOS and Windows wheels.
3. The real-fork and gevent tests pass, and so do free-threaded runs if CI has them.
4. Per-collection cost is below the plain-lock Python path on 3.9 and 3.15.
5. Every error path above has an injection test that fails without its handling.

Otherwise the Python path ships, and the native path lands in a later release.
