# Python Profiler Architecture

The profiler has a Python layer and a native layer.
The Python layer starts and stops the profiler, installs the hooks the collectors need, and schedules uploads.
The native layer samples stacks and allocations, builds samples, and hands them to libdatadog, which encodes and uploads the profile.

Paths below are relative to the repository root.

## Components

```
 Application threads, asyncio tasks, greenlets
   |            |              |              |
   | stack      | allocations  | locks        | exceptions, torch events
   v            v              v              v
 _stack       _memalloc      _lock.pyx      _exception.pyx, pytorch.py
 (sampling    (PyMem          (wrapped       (sys.monitoring,
  thread)      allocator)      lock classes)  torch.profiler)
   |            |              |              |
   |            |              +---- ddup.SampleHandle (Cython)
   v            v              v
 libdd_wrapper: Sample -> Profile -> Uploader
   |
   v
 libdatadog (inside ddtrace.internal.native._native) -> Datadog Agent
```

| Component | Path | Role |
|-----------|------|------|
| Product plugin | `ddtrace/internal/profiling/product.py` | Entry point (`profiler` in `pyproject.toml`). Installs the stack hooks early and starts the profiler when enabled. |
| Lifecycle | `ddtrace/internal/profiling/lifecycle.py` | Owns the bootstrap profiler and the lock that bootstrap and Remote Config hold while they replace it. |
| Remote Config | `ddtrace/internal/profiling/remoteconfig.py` | Merges profiling configurations by target specificity and restarts the profiler with the resulting collectors and settings. |
| Profiler | `ddtrace/profiling/profiler.py` | `Profiler` allows one active instance per process. `_ProfilerInstance` builds the collectors and the scheduler. |
| Scheduler | `ddtrace/profiling/scheduler.py` | Calls each collector's `snapshot()`, then `ddup.upload()`, every `DD_PROFILING_UPLOAD_INTERVAL` (60 s). `ServerlessScheduler` ticks every second and flushes after 60 ticks. |
| Collectors | `ddtrace/profiling/collector/` | See [Collectors](#collectors). |
| Thread, task and greenlet tracking | `ddtrace/profiling/collector/threading.py`, `_asyncio.py`, `_gevent.py`, `_threading.pyx` | Tell the stack sampler about threads, event loops, task links and greenlet switches. |
| dd_wrapper | `ddtrace/internal/datadog/profiling/dd_wrapper/` | C++ shared library over the libdatadog profiling FFI. Holds the process-global profile state. |
| ddup | `ddtrace/internal/datadog/profiling/ddup/` | Cython bindings to dd_wrapper: `config`, `start`, `upload`, `reset`, `SampleHandle`. |
| stack | `ddtrace/internal/datadog/profiling/stack/` | Native stack sampler built on a vendored copy of echion (`stack/echion/`, `stack/src/echion/`). |
| Code provenance | `ddtrace/internal/datadog/profiling/code_provenance.py`, `dd_wrapper/src/code_provenance.cpp` | Writes a JSON file that maps source paths to libraries. Each upload attaches it. |
| Native heap | `ddtrace/internal/datadog/profiling/heap_gotter/`, `src/native_heap_gotter/` | Patches the GOT so allocator calls fire `ddheap` USDT probes for an external eBPF profiler. Python does not collect or upload these samples. |

## Lifecycle

`product.py` runs in `post_preload`.
With `DD_PROFILING_ENABLED=armed` or the Remote Config experiment, it installs the thread, asyncio and gevent hooks at startup, so a profiler started later still sees threads, event loops and greenlets that already exist.
With profiling enabled, it imports `ddtrace.profiling.auto`, which calls `start_profiler()` in `ddtrace/profiling/bootstrap/sitecustomize.py`.

`start_profiler()` and the Remote Config callback both hold `lifecycle.lock` while they stop the current bootstrap profiler and set a new one.
With `ddtrace-run`, the Remote Config poller can start before `ddtrace.profiling.auto` is imported, so both can run at the same time.

`_ProfilerInstance._start_service()` starts these parts in order: native heap, `ddup.config()` and `ddup.start()`, collectors, scheduler.
The lock, asyncio lock and PyTorch collectors start when their module is first imported (`ModuleWatchdog`), so the profiler does not import `threading`, `asyncio` or `torch` before the application.
On stop, the scheduler stops and flushes a last profile before the collectors stop, because `snapshot()` needs them.
`Profiler.start()` also registers SIGTERM and SIGINT handlers that flush the last profile.

## Collectors

| Collector | Mechanism | Notes |
|-----------|-----------|-------|
| Stack (`stack.py`) | A native thread reads thread, asyncio task and greenlet stacks every 10 ms by default, then adapts the interval to a CPU overhead target. | Never takes the GIL. Links samples to the active span through `core` events. Records C calls on Python 3.12+ (`native_call_monitor.py`). |
| Memory (`memalloc.py`, `_memalloc*.cpp`) | Replaces the `PYMEM_DOMAIN_OBJ` allocator, and by default `PYMEM_DOMAIN_MEM` on Python 3.12+. Samples by bytes allocated, with exponentially distributed intervals. | Pushes each allocation sample at once. Keeps sampled live objects and exports them as heap samples in `snapshot()`. |
| Lock (`threading.py`, `asyncio.py`, `_lock.pyx`) | Replaces lock classes in `threading` and `asyncio` with a wrapper that returns profiled locks. | Records acquire wait time and hold time for `DD_PROFILING_CAPTURE_PCT` percent of operations. Patches again after `gevent.monkey.patch_all()`. |
| Exception (`_exception.pyx`) | `sys.monitoring` RAISE callback through `ddtrace.internal.monitoring`, with Poisson sampling. | Python 3.12+, GIL builds only. |
| PyTorch (`pytorch.py`) | Wraps `torch.profiler.profile` and converts the events of each trace into samples. | Adds GPU time, memory and FLOPs sample types. |

All collectors except stack and memory push samples through `ddup.SampleHandle`.
The stack sampler and memalloc call dd_wrapper directly.

## From sample to upload

A sample lives in a `Datadog::Sample` (`dd_wrapper/include/sample.hpp`).
`SampleManager::start_sample()` takes one from a fixed pool, or allocates one when the pool is empty.
The caller pushes values, frames and labels, then calls `flush_sample()`, which adds the sample to the profile under the profile lock.
`SampleManager::drop_sample()` returns it to the pool.
Each memalloc traceback embeds its own `Sample` and does not use the pool.

All samples go to one `ddog_prof_Profile` in `ProfilerState` (`dd_wrapper/src/profiler_state.cpp`).
At upload, `UploaderBuilder::build()` takes the profile lock only to swap the profiler stats and serialize the profile, which also resets it.
`Uploader` then sends the encoded profile to the Agent (or writes it to a file when `DD_PROFILING_OUTPUT_PPROF` is set) with a blocking libdatadog call on the scheduler thread.
It holds the upload lock while it sends, but not the profile lock, so sampling continues during the upload.
A new exporter is built for each upload.

## Fork, gevent, uWSGI and Gunicorn

The general fork-safety rules are in the "Fork Safety" section of [`ddtrace/internal/README.md`](../../../../../ddtrace/internal/README.md#fork-safety).

Native state is reset with `pthread_atfork` handlers, so it also works when the fork bypasses Python:

- dd_wrapper (`ProfilerState::prefork`): cancels an in-flight upload, then takes the upload lock and the profile lock. In the child it reinitializes the locks, drops and recreates the ProfilesDictionary, and resets the profile.
- stack (`Sampler::register_fork_cleanup`, `Sampler::one_time_setup`): in the child, it clears echion state and registered threads, registers the current thread, and restarts the sampling thread if it ran in the parent. Its handler is registered after dd_wrapper's, and POSIX runs child handlers in registration order, so the ProfilesDictionary exists again before sampling restarts.
- memalloc: resets the heap tracker in the child.

On the Python side, `Scheduler` is a `PeriodicService` that restarts after fork, and `lifecycle.lock` is a `forksafe.Lock`.
`_gevent.py` clears its greenlet maps in the child.

gevent: `_gevent.py` replaces `gevent.spawn`, `joinall`, `wait` and `iwait`, and installs a greenlet trace function in each thread.
It reports each switch to the sampler (`stack.record_greenlet_switch`), which then unwinds each greenlet on its own.
Thread hooks use `ddtrace.internal._unpatched._threading`, so gevent monkey-patching does not hide native threads.

uWSGI: without `lazy-apps` or `py-call-uwsgi-fork-hooks`, the master forks workers in C, and Python fork hooks do not run.
`uwsgi.check_uwsgi()` detects this, the profiler does not start in the master, and `_start_on_fork` starts it in each worker through `uwsgidecorators.postfork`.
Exception profiling stays off in these workers, because enabling `sys.monitoring` events walks thread states that only exist in the master.

Gunicorn forks with `os.fork()`, so the normal fork path applies.
The SIGTERM handler flushes the last profile when a worker exits.

## Design decisions

**The stack sampler runs off the GIL.**
A sampler that holds the GIL would only see other threads at points where they released it, which biases the profile.
A native thread samples threads wherever they are.
The cost is that it cannot call Python: it stores the error that stopped it, and `StackCollector.snapshot()` reports it.

**Interpreter memory is read with checked copies.**
The sampler reads memory that other threads change at the same time, so a read can hit an unmapped page.
`copy_memory` (`stack/echion/echion/vm.h`) uses `process_vm_readv` on Linux and `mach_vm_read_overwrite` on macOS, or a `memcpy` that a SIGSEGV/SIGBUS handler recovers from ("fast copy").
It falls back to the system call when another library owns those signal handlers.
`ddtrace/profiling/_faulthandler.py` pauses the sampler while `faulthandler` swaps handlers.

**echion is vendored.**
Sampler changes are made and tested in this repository, without a round trip through an upstream release.

**One libdd_wrapper.**
`_ddup`, `_stack` and `_memalloc` link one prebuilt `libdd_wrapper` (`setup.py` builds it first).
This gives one copy of the profile state and fork handlers, and keeps the wheel small.
dd_wrapper gets libdatadog from `_native` (`src/native`, `profiling` feature), which the tracer also uses, so the wheel ships libdatadog once.

**manylinux2014.**
The native code uses C++20 (`cmake/AnalysisFunc.cmake`), but wheels must pass the manylinux2014 auditwheel check.
The build containers have a newer compiler than the specification allows, so some library features (for example `std::filesystem`) build and then fail auditwheel.

**Symbol visibility.**
Assume that any library we link is also loaded by another module.
`add_ddup_config` links with `-Wl,--exclude-libs,ALL` and `-Wl,-Bsymbolic-functions`, so we do not export the symbols of static libraries and our calls do not bind to another module's copy.
`_memalloc` builds with `-fvisibility=hidden`.

**String lifetimes.**
Strings from Python, Cython or the sampler can be freed before the sample is flushed.
Function names and file names are interned in libdatadog's ProfilesDictionary, which outlives each profile.
Label values (thread name, task name, lock name) are copied into the sample's `StringArena`.

**Process-global native state is never destroyed.**
`ProfilerState::get()` and `Sampler::get()` allocate their instance once and never free it.
uWSGI can call `exit()` without running the Python hook that stops the sampler, and `sys.monitoring` callbacks can still run during finalization.

**Allocator hooks do not run Python code.**
memalloc runs inside CPython's allocator.
It reads frames through direct struct field reads and thread IDs through C APIs, skips nested calls with a thread-local guard, and disables the GC while it collects a traceback on Python 3.10 and 3.11.

## CPython internals

The stack sampler and memalloc read CPython internal structures (`Py_BUILD_CORE`), so each new CPython version needs a check.
Use the `find-cpython-usage` and `compare-cpython-versions` skills in `.agents/skills/`.

## Building and testing

See [Standalone.md](Standalone.md).
