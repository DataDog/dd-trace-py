Linux timer_create CPU stack profiler
=====================================

Status
------

Draft.

Context
-------

This document describes the design of the Linux `timer_create`-based CPU stack profiler for Python.


1. Executive summary
--------------------


2. Problem statement
--------------------


3. Goals and non-goals
----------------------


4. Architecture overview
------------------------


5. Core implementation details
------------------------------


6. Thread discovery and uvicorn worker fix
------------------------------------------


7. Async stitching
------------------


8. Safety and hardening
-----------------------


9. Compatibility and fallback behavior
--------------------------------------


10. Validation
--------------


11. Related work and prior art
------------------------------

### 11.1 Datadog java-profiler and async-profiler

The per-thread CPU timer implementation discussed here lives in the standalone
`DataDog/java-profiler` repository, under `java-profiler/ddprof-lib`.

Source versions inspected:

- `DataDog/java-profiler` default branch, `v_1.45.0 + 9 commits`, sha
  `b045bb26d93294446cfa552d9b54e3a935ea45de`.
- `async-profiler/async-profiler` default branch, tag `nightly`, sha
  `656d96b57f1bcd7e54dee04d61985d340d6b93c3`.

#### async-profiler ctimer

async-profiler shares implementation lineage with Datadog java-profiler's CTimer
engine. It exposes three CPU sampling engines: `cpu`, `itimer`, and `ctimer`.
The [`CpuSamplingEngines.md`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/docs/CpuSamplingEngines.md)
document describes `ctimer` as a Linux-specific alternative to `perf_events` for
environments where `perf_event_open` is blocked by `kernel.perf_event_paranoid`,
seccomp, or file descriptor limits. That is the same deployment tradeoff that
matters for dd-trace-py: `perf_event_open` is attractive, but it is not reliably
available in arbitrary customer containers.

The async-profiler [`CTimer::createForThread`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/ctimer_linux.cpp#L31-L63)
implementation uses the same core Linux primitive as our Python CPU timer:

- [`thread_cpu_clock`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/ctimer_linux.cpp#L23-L25)
  encodes an arbitrary Linux tid into a per-thread CPU clock id with
  `((~tid) << 3) | 6`.
- `CTimer::createForThread` uses raw `__NR_timer_create`, because libc only
  accepts predefined clock ids.
- It uses `SIGEV_THREAD_ID` to deliver the profiling signal to the target tid.
- It stores one timer id per tid and arms each timer periodically with raw
  `__NR_timer_settime`.

Thread lifecycle is handled outside the timer primitive. [`CTimer::start`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/ctimer_linux.cpp#L76-L117)
installs the signal handler, enables a thread hook, and creates timers for all
existing threads. The hook is implemented in [`cpuEngine.cpp`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/cpuEngine.cpp#L24-L89):
async-profiler patches the JVM's imported `pthread_setspecific` entry. On
HotSpot, the JVM stores the `VMThread` pointer in TLS on thread start and clears
it on thread end, so the hook observes those transitions and calls
[`CpuEngine::onThreadStart`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/cpuEngine.cpp#L44-L49)
and [`CpuEngine::onThreadEnd`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/cpuEngine.cpp#L51-L56).

The engine selection path also encodes the container story. For `-e cpu`,
[`Profiler::selectEngine`](https://github.com/async-profiler/async-profiler/blob/656d96b57f1bcd7e54dee04d61985d340d6b93c3/src/profiler.cpp#L741-L750)
uses `perf_events` when available, falls back to `ctimer` when `perf_events` is
not available, and then falls back to wall-clock sampling. The important lesson
for Python is that `timer_create` is not just an accuracy mechanism. It is also a
practical fallback for locked-down Linux deployments.

#### Datadog java-profiler CTimer

Datadog java-profiler uses the same timer shape as async-profiler CTimer, but
adds production hardening that influenced the Python implementation.

The core timer registration path is [`CTimer::registerThread`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/ctimer_linux.cpp#L54-L117):

- it zeroes the whole `sigevent` struct before initialization,
- it writes a profiler-owned cookie into `sev.sigev_value.sival_ptr`,
- it sets `sigev_notify = SIGEV_THREAD_ID`,
- it writes the target tid into the libc-specific `sigev_notify_thread_id` slot,
- it creates the timer through raw `__NR_timer_create`,
- it publishes the timer in a tid-indexed table with CAS,
- it arms the timer through raw `__NR_timer_settime`,
- it reclaims the timer if arming fails after publication.

The CPU engine selection in [`Profiler::selectCpuEngine`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/profiler.cpp#L1091-L1135)
prefers CTimer first, then `perf_events`, then `ITimer`. That order is
significant. [`ITimer::signalHandler`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/itimer.cpp#L33-L40)
uses process-wide `setitimer(ITIMER_PROF)`, whose signals cannot carry a
`sigval` payload. Therefore ITimer cannot perform the same origin validation as
CTimer.

The CTimer cookie is defined through [`SignalCookie`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/signalCookie.h#L7-L23).
The signal-origin validation plan explains the bug class that motivated it:
[`doc/plans/SignalOriginValidation.md`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/doc/plans/SignalOriginValidation.md#L1-L37)
describes a Go-cgo shared library loaded into the JVM that starts dd-trace-go CPU
profiling. Go's process-wide `setitimer(ITIMER_PROF)` can deliver `SIGPROF` to
arbitrary JVM threads. If java-profiler processed every `SIGPROF` as its own,
the handler could run on threads it never registered. The documented failure
mode was first-touch access to profiler `thread_local` state from such a handler,
which could call `__tls_get_addr` and allocate while the interrupted thread held
a malloc lock.

The Datadog [`CTimer::signalHandler`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/ctimer_linux.cpp#L251-L303)
therefore validates signal origin before doing profiler work. Foreign signals
increment a counter and are forwarded to the previous handler. Own signals enter
a critical section, save `errno` before the sampling path, find the current
profiled thread if possible, record a CPU sample weighted by the configured
interval, and restore `errno` after the normal sampling path. This maps directly
onto Python's use of a `SIGPROF` cookie: if another library or the application
also uses `SIGPROF`, the handler needs a way to distinguish timer-originated
signals from foreign signals.

Datadog java-profiler also demonstrates that per-thread timer registration has
runtime metadata races. In [`CTimer::signalHandler`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/ctimer_linux.cpp#L276-L287),
the handler guards the race window between `Profiler::registerThread()` and JVM
TLS initialization, tracked as `PROF-13072`, by skipping at most one signal while
the thread is in that initialization window. The Python implementation has a
different runtime model, but the lesson is the same: creating the OS timer is not
enough. The handler must not assume all runtime thread metadata is usable just
because the native tid has a timer.

The java-profiler thread lifecycle path also influenced our shutdown and thread
teardown thinking. [`Profiler::onThreadStart`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/profiler.cpp#L75-L91)
registers the current tid with the CPU and wall engines. [`Profiler::onThreadEnd`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/profiler.cpp#L93-L130)
blocks profiling signals around engine unregistration and TLS release to avoid
sampling a partially torn-down thread, tracked as `PROF-14674`. Separately,
[`Profiler::_instance`](https://github.com/DataDog/java-profiler/blob/b045bb26d93294446cfa552d9b54e3a935ea45de/ddprof-lib/src/main/cpp/profiler.cpp#L55-L57)
is intentionally not deleted because profiler structures can still be accessed
during VM termination. This is the same class of shutdown hazard we saw for
Python, especially with embedders and uWSGI-like lifecycles. Late profiling
signals are worse than small process-lifetime leaks if freeing global state can
turn a late signal into a use-after-free.

#### Implications for the Python design

The Java and async-profiler implementations support the main shape of the Python
CPU timer design:

- Use raw Linux syscalls for arbitrary per-thread CPU clocks.
- Use `SIGEV_THREAD_ID` so the signal is delivered to the thread that consumed
  CPU.
- Treat signal-origin validation as required, not optional.
- Use explicit reentrancy or critical-section guards in the signal handler, and
  keep handler work constrained.
- Avoid clobbering signal-handler ambient state such as `errno`.
- Register already-running threads and future threads.
- Expect runtime-specific thread metadata races and handle them explicitly.
- Prefer safe lifetime behavior during shutdown over reclaiming every byte.

Python differs in where the complexity lands. Java has JVM thread lifecycle
callbacks, JVM TLS, and ASGCT/JVMTI stack walking. Python has CPython
`PyThreadState` discovery, frame/code object lifetime hazards, async logical
stack stitching, fork-heavy worker models, and application frameworks such as
uvicorn that can start profiler-relevant work on already-existing main threads.
The prior art validates the timer mechanism, but Python still needs its own
thread discovery, signal safety, frame-read guarding, and logical-stack
integration.

### 11.2 Go runtime CPU profiler

Go is the most important non-Datadog runtime precedent for per-thread CPU-time
profiling. Starting in Go 1.18, the Linux runtime uses one CPU timer per `M`.
`M` is Go runtime terminology for an OS thread, used as part of Go's G/M/P
scheduler model: goroutines (`G`) run on machine threads (`M`) through logical
processors (`P`). The change was motivated by Go issue
[35057](https://go.dev/issue/35057), which is referenced directly in the current
runtime source: process-wide `setitimer(ITIMER_PROF)` does not attribute CPU
fairly under high parallelism, while per-thread timers report CPU usage for the
thread that actually consumed it. Datadog profiling engineer Felix Geisendörfer
contributed to this Go 1.18 CPU profiling work and wrote about it in
[Profiling improvements in Go 1.18](https://www.datadoghq.com/blog/engineering/profiling-improvements-in-go-1-18/).

Source version inspected: `golang/go` default branch, sha
`63b51fc270d9d061d823ae274de15b8af8b3a54d`, committed 2026-06-25.

The Linux implementation lives in [`runtime/os_linux.go`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/os_linux.go).
[`setThreadCPUProfiler`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/os_linux.go#L638-L706)
performs the per-M setup:

- it deletes any existing timer for the current M before reconfiguring it,
- it randomizes the initial expiry in `(0, period]`,
- it configures `sigev_notify = SIGEV_THREAD_ID`,
- it delivers `SIGPROF` to `mp.procid`, the Linux tid backing the M,
- it calls `timer_create(_CLOCK_THREAD_CPUTIME_ID, ...)`,
- it arms the timer with `timer_settime`,
- it records `profileTimerValid` only after successful creation and arming.

Go differs from dd-trace-py in one important way. Go calls
`setThreadCPUProfiler` while running on the M being configured. Because the
caller is the target thread, Go can use `_CLOCK_THREAD_CPUTIME_ID`, the current
thread's CPU clock. dd-trace-py sometimes has to arm timers for threads
discovered from another thread, such as the sampler thread finding an existing
Python main thread. That is why dd-trace-py and java-profiler use an encoded
arbitrary-tid CPU clock with a raw syscall for remote arming.

Go still keeps the process-wide `setitimer` profiler active. The process-wide
path is configured in [`setProcessCPUProfilerTimer`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/signal_unix.go#L305-L311).
The per-thread timer is the preferred source for Go-managed threads, but the
process-wide timer remains useful for threads that do not have an active M or
where a per-thread timer could not be created. If `timer_create` fails in
`setThreadCPUProfiler`, Go leaves `profileTimerValid` false and falls back to the
process-wide `setitimer` path for that M.

Because Go can receive `SIGPROF` from both mechanisms, it has explicit signal
source arbitration. [`validSIGPROF`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/os_linux.go#L584-L632)
checks the signal code:

- `_SI_TIMER` means the signal came from a per-thread `timer_create` timer,
- `_SI_KERNEL` means the signal came from process-wide `setitimer`,
- other signal sources are not treated as Go runtime profiling mechanisms.

If the current M has an active per-thread timer, Go accepts `_SI_TIMER` and
ignores the process-wide `setitimer` signal for that M to avoid double-counting.
If there is no active per-thread timer, Go accepts the process-wide `setitimer`
signal. If the signal arrives on a thread without an M, Go cannot know whether a
per-thread timer exists for that thread, so it processes only `setitimer` signals.
This is a direct runtime-level example of the same issue we handle with the
Python CPU timer cookie: a process can have multiple `SIGPROF` sources, and the
handler must decide which ones belong to it.

Go also updates per-thread profiling state when execution reaches paths that do
not go through the normal scheduler loop. For example, [`cgocallbackg1`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/cgocall.go#L439-L444)
checks whether the current M's profiling rate matches the scheduler rate and
calls `setThreadCPUProfiler` if needed. This matters for cgo and extra threads
that enter Go from C.

Another lesson is deadlock avoidance during global profiling-rate changes. In
[`SetCPUProfileRate`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/proc.go#L5928-L5950),
Go disables the profiler on the current thread before taking the global profiler
signal lock, because a profiling signal while holding that lock could deadlock.
This mirrors the broader rule in signal-driven profilers: do not let the signal
handler interrupt code that holds locks the handler may need.

Finally, Go's random initial expiry is relevant to sampling quality. The long
comment in [`setThreadCPUProfiler`](https://github.com/golang/go/blob/63b51fc270d9d061d823ae274de15b8af8b3a54d/src/runtime/os_linux.go#L660-L678)
explains that starting every timer at exactly one full period biases against
short-lived or occasionally active threads. By sampling the first expiry
uniformly in `(0, period]`, Go gives partial-period CPU work proportional sample
probability. dd-trace-py does not currently randomize the first CPU timer expiry,
but the Go behavior is useful future work if we observe bias against short-lived
Python threads.

Implications for dd-trace-py:

- Per-thread CPU timers fix the same attribution problem across runtimes: the
  timer advances only while the target thread consumes CPU.
- A process-wide `setitimer` fallback has different semantics and must not be
  mixed blindly with per-thread timers, or CPU can be double-counted.
- Signal source classification is required whenever multiple `SIGPROF` sources
  can exist in one process.
- Thread lifecycle coverage must include unusual entry paths, such as cgo in Go
  or uvicorn/spawn/main-thread discovery in Python.
- Disabling profiling around global locks or teardown paths is a recurring
  safety pattern.
- Randomized initial expiry can reduce bias for short-lived threads.

### 11.3 Datadog .NET profiler

Datadog's .NET profiler is the closest Datadog implementation outside Java. Its
Linux default CPU profiler is `TimerCreateCpuProfiler`, a per-thread
`timer_create` implementation with many of the same lifecycle and signal-handler
constraints as dd-trace-py.

Source version inspected: `DataDog/dd-trace-dotnet` default branch,
`v3.47.0 + 3 commits`, sha `e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a`.

The Linux default is configured in [`Configuration::DefaultCpuProfilerType`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native/Configuration.cpp#L29-L35):
Windows uses `ManualCpuTime`, while non-Windows defaults to `TimerCreate`. The
native profiler is constructed on Linux from [`CorProfilerCallback`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native/CorProfilerCallback.cpp#L640-L655)
when CPU profiling is enabled and the configured CPU profiler type is
`TimerCreate`.

The core timer registration path is [`TimerCreateCpuProfiler::RegisterThreadImpl`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native.Linux/TimerCreateCpuProfiler.cpp#L329-L384):

- it stores one timer id on each `ManagedThreadInfo`,
- it targets the managed thread's OS tid,
- it sets `sigev_value.sival_ptr = nullptr`, so this path does not use a timer
  cookie,
- it sets `sigev_signo` from `ProfilerSignalManager::Get(SIGPROF)`,
- it sets `sigev_notify = SIGEV_THREAD_ID`,
- it writes the target tid into the `sigev_notify_thread_id` slot,
- it encodes a per-thread CPU clock with `((~tid) << 3) | 6`,
- it calls raw `__NR_timer_create`, because libc only accepts predefined clocks,
- it arms the timer with raw `__NR_timer_settime`,
- it deletes timers with raw `__NR_timer_delete` in [`UnregisterThreadImpl`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native.Linux/TimerCreateCpuProfiler.cpp#L386-L395).

Thread lifecycle is driven by CLR profiler callbacks. In
[`ThreadAssignedToOSThread`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native/CorProfilerCallback.cpp#L2319-L2325),
.NET registers the thread with the timer-create profiler only after setting the
managed thread's OS-thread information. The source comment is blunt: if the
registration happens earlier, `threadInfo` does not have its OS thread field set
and `timer_create` has random behavior. This mirrors the lesson from Python's
uvicorn fix: timer arming must be coupled to correct runtime/native thread
identity, not just to an approximate thread lifecycle event.

The handler path is [`TimerCreateCpuProfiler::Collect`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native.Linux/TimerCreateCpuProfiler.cpp#L255-L315).
It shows several signal-safety patterns that also apply to Python:

- it increments an in-handler counter so shutdown can detect active handlers,
- it requires `ManagedThreadInfo::CurrentThreadInfo` to be available,
- it uses [`StackWalkLock`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native.Linux/TimerCreateCpuProfiler.cpp#L226-L253),
  which tries to acquire a per-thread lock and drops the sample on contention
  rather than blocking in a signal handler,
- it calls [`CanCollect`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native.Linux/TimerCreateCpuProfiler.cpp#L189-L208)
  before unwinding,
- it saves and restores `errno`, because libunwind can overwrite it,
- it unwinds from the interrupted context and gives the sample the configured
  sampling interval as its duration.

`CanCollect` contains two especially relevant safety gates. First, it checks the
weak `dd_inside_wrapped_functions` symbol and skips collection if the interrupted
thread is inside a known wrapped function that could deadlock. Second, it checks
whether `SIGSEGV` is already present in the interrupted context's signal mask and
skips collection if the thread appears to be running a SIGSEGV handler. The
Python implementation arrived at the same broad policy from a different runtime:
if a signal handler runs at an unsafe point, prefer dropping or disabling samples
over taking locks, allocating, or walking fragile state.

The shutdown path is also directly relevant. [`TimerCreateCpuProfiler::StopImpl`](https://github.com/DataDog/dd-trace-dotnet/blob/e6d7b7f6997b7b5aba5cc1b98b4ca94135735a2a/profiler/src/ProfilerEngine/Datadog.Profiler.Native.Linux/TimerCreateCpuProfiler.cpp#L137-L170)
sets the singleton instance to null, marks `SIGPROF` as ignored, deletes all
per-thread timers, and waits up to 500 ms for active signal handlers to exit. The
source comment records the concrete failure mode: if timers remain live while the
handler/disposition is torn down, the process can exit with code 155, which is
`128 + SIGPROF`. This is the same class of bug that led dd-trace-py to prefer
process-lifetime state and conservative shutdown behavior over freeing all CPU
timer state eagerly.

The .NET rollout history is useful because it shows that the timer primitive
worked before all lifecycle issues were solved. PR
[#7322](https://github.com/DataDog/dd-trace-dotnet/pull/7322) made the
`timer_create` CPU profiler the Linux default after a period of CI exposure.
That change was reverted in PR
[#7427](https://github.com/DataDog/dd-trace-dotnet/pull/7427) after flaky
crashes in trimmed smoke tests. PR
[#7578](https://github.com/DataDog/dd-trace-dotnet/pull/7578) reintroduced the
default after two important fixes: only register managed threads assigned to
native threads, and make the ring buffer long-lived so it can outlive individual
consumers. Those are exactly the kinds of non-obvious lifecycle issues that are
easy to miss in a signal-driven profiler.

Implications for dd-trace-py:

- The raw syscall and `SIGEV_THREAD_ID` shape is shared across Java, Go, .NET,
  and Python.
- Correct thread identity must be established before timer arming.
- Existing threads must be registered at profiler start, and future threads need
  lifecycle hooks or native discovery.
- Signal handlers should use non-blocking guards and drop samples rather than
  wait on locks.
- Safety gates for known unsafe interrupted contexts are worth adding, even if
  they reduce sample count.
- Shutdown ordering matters: timers, handlers, and in-flight signal handlers
  must be handled as one system.
- Rollout should expect lifecycle bugs after the timer itself appears to work.

### 11.4 OpenJDK JFR CPU-time profiling

### 11.5 gperftools per-thread timer mode

### 11.6 ddprof and perf_event_open task-clock profiling

### 11.7 Out-of-process and eBPF profilers

### 11.8 Other Python profilers

### 11.9 Common lessons from prior implementations


12. Alternatives
----------------


13. Rollout and future work
---------------------------


14. Appendices
--------------
