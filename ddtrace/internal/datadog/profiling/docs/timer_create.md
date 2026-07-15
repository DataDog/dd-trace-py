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

Each prepared Python thread owns a Linux per-thread CPU timer and a preallocated
single-producer, single-consumer ring. `SIGEV_THREAD_ID` delivers `SIGPROF` to
the thread that consumed CPU. The signal handler captures raw physical Python
frame metadata into that thread's ring without allocating or locking. The
sampler thread later validates the raw code pointers, reconstructs frames,
stitches logical task or greenlet stacks, and renders the CPU sample.

Wall sampling and CPU timer draining share the existing sampler thread but use
independent deadlines. Wall-stack collection keeps its adaptive interval. CPU
rings drain every 10 ms while the CPU timer engine is active, independently of
the configured CPU timer signal interval. Keeping one sampler thread avoids a
second profile writer, additional fork synchronization, and new shutdown
lifetime concerns.

CPU timer profiling has priority within the profiler overhead budget. The CPU
timer signal interval and 10 ms drain cadence remain fixed during the initial
experiment. Adaptive sampling controls only wall-stack collection, so it can
reduce wall sampling frequency to compensate for CPU timer drain and rendering
cost.


5. Core implementation details
------------------------------

### 5.1 Sampling and drain cadence

The configured CPU timer interval controls how often a continuously running
thread receives a CPU-time signal. It does not control wall-stack collection or
ring draining. The sampler thread tracks separate wall and CPU-drain deadlines:

- The wall deadline uses the existing adaptive `sample_interval_us`, up to the
  configured 100 ms maximum.
- The CPU-drain deadline is fixed at 10 ms while the engine is active.
- The sampler sleeps until the earlier deadline and performs only the work that
  is due. When both are due, it drains CPU rings before starting the wall-stack
  walk so wall collection does not add avoidable ring latency.

A 10 ms drain period bounds deferred raw-pointer and async-stitching latency. At
the private 2 ms minimum timer interval, a continuously CPU-active thread
normally accumulates about five samples between drains, well below the ring's
63 usable slots.

Adaptive sampling continues to compare total sampler-thread CPU against process
CPU. Total sampler CPU includes both wall collection and CPU drain/render work,
but only the wall interval is adjusted. CPU processing therefore forms a fixed
overhead floor, and wall sampling backs off first. Internal profile metadata
reports the total together with `wall_sample_capture_cpu_time_us` and
`cpu_timer_drain_cpu_time_us` components. Signal-handler CPU executes on the
profiled application threads and is not included in the sampler-thread
components.

If fixed CPU processing alone exceeds the overhead target after wall sampling
reaches its maximum interval, the initial private experiment preserves CPU
sampling accuracy and reports the component costs for evaluation. A later
rollout may widen the CPU timer interval as a last resort, but it must not
silently claim that the overhead target was met.


6. Thread discovery and uvicorn worker fix
------------------------------------------


7. Async stitching
------------------


8. Safety and hardening
-----------------------

### 8.1 Residual EINTR and native syscall risk

The CPU timer profiler uses `SIGPROF`, so it shares the broad syscall
interruption risk of other signal-based profilers. `SA_RESTART` and CPython's
PEP 475 retry behavior reduce the risk for Python-level syscall APIs, but they
do not protect arbitrary native extension code that issues syscalls directly and
does not retry `EINTR`.

The risk differs by thread type:

- Pure native pthreads that never acquire Python thread state are not expected to
  be armed. They have no `PyThreadState`, so they should not be discovered by the
  CPU timer thread registration path.
- Python-visible threads can be armed. If they call CPython syscall wrappers,
  PEP 475 usually retries interrupted syscalls when no Python signal exception is
  raised.
- Python-visible threads running native extension code can still observe
  interruption if that native code uses raw syscalls or libc calls without
  retrying `EINTR`.
- Threads blocked off-CPU are lower risk because per-thread CPU timers do not
  advance while the thread is not consuming CPU. On-CPU syscalls and short
  syscall-heavy loops remain in scope.

Current mitigations:

- Install the `SIGPROF` handler with `SA_RESTART`.
- Use CPU-time timers rather than wall-clock timers, reducing signal delivery
  while a thread is blocked off-CPU.
- Clamp the effective minimum interval to 2 ms. The default remains 10 ms.
- Keep the feature off by default behind `_DD_PROFILING_STACK_CPU_TIMER_ENABLED`.
- Use signal-origin cookie validation and foreign `SIGPROF` forwarding. In a
  post-fork child, foreign `SIGPROF` forwarding is disabled: only the forking
  thread survives, and an inherited handler can depend on runtime state owned by
  vanished parent threads. Own CPU-timer signals still carry the profiler cookie
  and are handled normally.
- Do not arm pure native threads that are not visible to CPython.
- Cover Python-level syscall behavior with
  `test_cpu_timer_syscall_readv_loop_does_not_surface_eintr`.
- Cover pure native pthread behavior with
  `test_cpu_timer_does_not_arm_raw_pthread_that_never_enters_python`.
- Keep native syscall hazard reproducers in
  `tests/profiling/cpu_timer_native_syscall_hazard_app.py`. The associated
  xfail,
  `test_cpu_timer_raw_native_ppoll_without_eintr_retry_is_not_interrupted`,
  demonstrates that a Python-visible native function can accumulate a pending
  CPU timer `SIGPROF`, atomically unblock it inside raw `ppoll`, and observe
  `EINTR` if it does not retry.
- Cover the `read`/`readv` and `nanosleep`/`clock_nanosleep` variants with
  `test_cpu_timer_raw_native_read_without_eintr_retry_is_not_interrupted` and
  `test_cpu_timer_raw_native_nanosleep_without_eintr_retry_is_not_interrupted`.
  These record that, unlike `ppoll`, those syscalls take no signal mask, so
  there is no atomic unblock-inside-the-syscall race: the pending `SIGPROF` is
  delivered when it is unblocked, before the syscall, and the syscall then
  blocks off-CPU where the per-thread CPU timer does not advance. They pass
  (no `EINTR`), which is the recorded result for the current Linux targets.
- Run native-heavy ecosystem workloads before enabling this more broadly.
- Permanently disable CPU-timer mode if health windows show a sustained high
  rate of capture failures or ring overflows. This keeps pathological
  environments from emitting failure-only timer traffic indefinitely.

Required hardening before considering broader rollout:

1. Expand native extension syscall coverage. Done for the initial set: raw
   `ppoll` (atomic unblock inside the syscall, surfaces `EINTR`, xfail) plus
   `read`/`readv` and `nanosleep`/`clock_nanosleep` (no signal-mask argument,
   do not surface `EINTR` on the current Linux targets). Extend to further
   syscalls (for example `recv`/`sendmsg`, `futex`-backed waits) if ecosystem
   testing shows a concrete gap.
2. Add event-loop and networking stress coverage with CPU timer enabled,
   including asyncio, uvloop, Trio or AnyIO if available, and representative HTTP
   client/server workloads.
3. Add debug counters for skipped thread arming reasons, own and foreign
   `SIGPROF` counts, ring overflow, reentrant drops, and timer overruns. Timer
   overruns are now tracked: `timer_overrun_total` sums `si_overrun` (missed
   expirations that coalesced into a delivered signal) and
   `coalesced_signal_count` counts signals with `si_overrun > 0`. These are a
   sampling-quality signal only. Because `cpu_delta_ns` is measured from the
   thread CPU clock, it already conserves the CPU consumed during coalesced
   expirations, so overruns are not used to weight samples (doing so would
   double-count CPU). A rising overrun rate indicates the handler or interval is
   dropping sampling resolution and is a cue to widen the interval. Sustained
   high capture-failure or ring-overflow windows disable CPU-timer mode and are
   reported with `health_disable_count`.
4. Keep the 2 ms minimum interval until the native syscall and ecosystem tests
   show that a lower interval is safe. Do not expose the interval as public API
   while this remains experimental.
5. Document the residual risk for native extensions: packages that do not retry
   `EINTR` may still be affected by any signal-based CPU profiler.
6. Consider a current-thread pause/resume API only for Datadog-owned native code
   or specific integrations. Do not rely on it to protect arbitrary packages.
7. Consider syscall shielding only if tests or production experiments show a
   concrete need. It is a larger design than the timer profiler itself.


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

OpenJDK has an in-JVM CPU-time sampling path for JFR. It is related to this
design because it uses Linux per-thread CPU timers, but it differs from
dd-trace-py in how it obtains the per-thread CPU clock and how it turns a signal
into a stack trace.

Source version inspected: `openjdk/jdk` default branch, `jdk-28+4 + 11 commits`,
sha `892c881258280e08273cafc5c111ac50388200fc`. The public design reference is
[JEP 509: JFR CPU-Time Profiling](https://openjdk.org/jeps/509).

The JFR event is [`jdk.CPUTimeSample`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/metadata/metadata.xml#L993-L1001).
In the current metadata it is marked `experimental="true"`, includes both native
and Java code according to its description, carries the CPU sampling period, and
has a `biased` field whose description is "The sample is safepoint-biased". The
sample is present in the default JFR configuration but disabled by default in
[`default.jfc`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/jdk.jfr/share/conf/jfr/default.jfc#L234-L238),
with a default throttle setting of `500/s`.

The timer setup lives in [`JfrCPUSamplerThread::create_timer_for_thread`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L672-L691):

- it sets `sigev_notify = SIGEV_THREAD_ID`,
- it delivers `SIGPROF`,
- it sets `sigev_value.sival_ptr = nullptr`, so this path does not use a timer
  cookie,
- it writes the target Java thread's OS thread id into the `SIGEV_THREAD_ID`
  target slot,
- it calls `pthread_getcpuclockid(thread->osthread()->pthread_id(), &clock)`,
- it calls libc `timer_create(clock, &sev, &timerid)`,
- it arms the timer with `timer_settime` through [`set_timer_time`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L656-L670).

That clock setup is different from dd-trace-py, java-profiler, and .NET. Those
implementations encode a Linux tid into a CPU clock id and call the raw
`timer_create` syscall. OpenJDK instead asks pthreads for the CPU clock id of the
target pthread with `pthread_getcpuclockid`, then passes that clock id to libc
`timer_create`. The signal target is still the OS thread selected through
`SIGEV_THREAD_ID`.

OpenJDK also has explicit lifecycle handling. [`JfrCPUSamplerThread::on_javathread_create`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L304-L321)
initializes the per-thread CPU-time queue and creates the timer if the signal
handler is installed. [`on_javathread_terminate`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L323-L338)
unsets the CPU timer, deallocates the queue, and emits a lost-samples event if
needed. Thread-local start and exit hooks call into this path from
[`JfrThreadLocal::on_start`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/support/jfrThreadLocal.cpp#L145-L151)
and [`JfrThreadLocal::on_exit`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/support/jfrThreadLocal.cpp#L244-L260).

Startup and shutdown are coordinated through VM operations. [`init_timers`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L740-L752)
checks the existing `SIGPROF` handler before installing OpenJDK's generic signal
handler. If another non-default, non-ignored, non-OpenJDK handler is already
installed, it logs that `CPUTimeSample` events will not be recorded. It then runs
a VM operation over existing Java threads to create timers. [`stop_timer`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L754-L780)
runs a VM operation that deallocates queues and unsets per-thread timers. The
unset path calls `timer_delete` in [`JfrThreadLocal::unset_cpu_timer`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/support/jfrThreadLocal.cpp#L595-L600).
[`stop_signal_handlers`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L695-L702)
sets a stop bit and waits for active signal handlers to finish.

The signal handling path is deliberately split. [`JfrCPUTimeThreadSampling::handle_timer_signal`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L570-L582)
accepts only `SI_TIMER`, increments an active-handler count, delegates to the
sampler, and decrements the count afterward. [`JfrCPUSamplerThread::handle_timer_signal`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrCPUTimeThreadSampler.cpp#L609-L653)
then:

- finds the current Java thread if valid,
- accepts only `_thread_in_Java` and `_thread_in_native`,
- tries to acquire the per-thread CPU-time enqueue lock,
- computes the actual CPU period using `si_overrun + 1`,
- builds a sample request from the interrupted context,
- enqueues the request in the thread-local CPU-time queue,
- arms a local safepoint poll for Java threads,
- requests asynchronous processing for native threads.

The stack trace is not fully recorded in the signal handler. The queued request
is drained later by [`drain_enqueued_cpu_time_requests`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrThreadSampling.cpp#L350-L374),
which calls [`record_cpu_time_thread`](https://github.com/openjdk/jdk/blob/892c881258280e08273cafc5c111ac50388200fc/src/hotspot/share/jfr/periodic/sampling/jfrThreadSampling.cpp#L300-L332).
That path computes the top frame, records a JFR stack trace, emits either a
`CPUTimeSample` or an empty failed event, and reports safepoint latency when the
current thread is the sampled Java thread.

Implications for dd-trace-py:

- OpenJDK validates the same high-level timing model: Linux per-thread CPU timers
  can drive CPU-time stack sampling inside a managed runtime.
- OpenJDK avoids doing full stack trace recording in the signal handler. It
  enqueues a request and performs stack trace work later through VM/JFR paths.
- OpenJDK explicitly tracks lost samples, queue-full drops, active signal
  handlers, and timer creation failures.
- OpenJDK treats an existing conflicting `SIGPROF` handler as a reason not to
  record `CPUTimeSample` events. dd-trace-py takes a different approach with
  cookie validation and foreign-signal forwarding, but the shared fact is that
  `SIGPROF` ownership must be handled deliberately.
- The event's `biased` field and the safepoint-drain path make the possible
  safepoint bias explicit. dd-trace-py's current in-handler frame capture trades
  that bias concern for CPython frame-read and signal-safety hazards.

### 11.5 gperftools per-thread timer mode

gperftools is useful prior art because it supports a per-thread POSIX timer mode,
but that mode is opt-in rather than the default. The default CPU profiler uses
process-wide interval timers: `ITIMER_PROF` by default, or `ITIMER_REAL` when
`CPUPROFILE_REALTIME` is set.

Source version inspected: `gperftools/gperftools` default branch,
`gperftools-2.18.1 + 17 commits`, sha
`07c5e9226bda1720bdf783a11f5df0f515e3c9d3`.

The public interface documentation in [`profile-handler.h`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.h#L35-L45)
states the high-level contract:

- all threads in the program are profiled when enabled,
- registered callbacks must be async-signal-safe,
- the module requires sole ownership of the configured timer and signal,
- the timer defaults to `ITIMER_PROF`,
- `CPUPROFILE_REALTIME` changes it to `ITIMER_REAL`,
- `CPUPROFILE_PER_THREAD_TIMERS` changes it to a POSIX timer,
- `CPUPROFILE_TIMER_SIGNAL` can select a custom signal only with per-thread
  timers.

The user documentation for CPU profiling lists `CPUPROFILE_FREQUENCY` and
`CPUPROFILE_REALTIME`, and recommends `ITIMER_PROF` over `ITIMER_REAL` unless the
user has a reason to prefer realtime sampling. That is documented in
[`docs/cpuprofile.adoc`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/docs/cpuprofile.adoc#L86-L96).

The per-thread timer mode is configured in [`ProfileHandler::ProfileHandler`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L305-L347).
If either `CPUPROFILE_PER_THREAD_TIMERS` or `CPUPROFILE_TIMER_SIGNAL` is present,
gperftools enables per-thread timer mode, but only if the weak `timer_create`
symbol is available. If `timer_create` is not available, the code logs that the
per-thread timer settings are ignored and suggests preloading or linking
`librt.so`.

Timer creation is in [`StartLinuxThreadTimer`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L259-L290):

- it zeroes the `sigevent`,
- it sets `sigev_notify = SIGEV_THREAD_ID`,
- it targets the current Linux tid with `syscall(SYS_gettid)`,
- it uses the configured signal number,
- it uses `CLOCK_THREAD_CPUTIME_ID` for `ITIMER_PROF` mode,
- it uses `CLOCK_MONOTONIC` for `ITIMER_REAL` mode,
- it calls libc `timer_create`, not a raw syscall,
- it stores the resulting timer id in thread-local storage,
- it arms the timer with `timer_settime`.

The thread-local timer id is important. gperftools creates a TLS key with a
destructor in [`CreateThreadTimerKey`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L252-L257),
and the destructor calls `timer_delete` through [`ThreadTimerDestructor`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L241-L249).
`ProfileHandler::RegisterThread` starts the per-thread timer for the current
thread when per-thread mode is enabled. The main thread is registered by a module
initializer, [`profile_main`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L526-L530).

The process-wide timer path is still visible in [`UpdateTimer`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L474-L493).
When per-thread timer mode is enabled, `UpdateTimer` ignores attempts to enable
or disable the timer, with the source comment noting that disabling is not
supported and the per-thread timers are always enabled.

The signal handler model is callback-based. [`ProfileHandler::SignalHandler`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L509-L524)
saves `errno`, increments the interrupt count, and invokes all registered
callbacks under `signal_lock_`, then restores `errno`. The public header states
that callbacks must be async-signal-safe and must not call back into
`ProfileHandler` functions. It also says callback code must not acquire locks to
serialize access to data shared with non-handler code. Instead, non-handler code
should unregister the callback, modify the shared data, and re-register the
callback.

gperftools also claims sole ownership of the configured timer and signal. Before
installing its handler, [`IsSignalHandlerAvailable`](https://github.com/gperftools/gperftools/blob/07c5e9226bda1720bdf783a11f5df0f515e3c9d3/src/profile-handler.cc#L495-L507)
checks that the current handler is `SIG_IGN` or `SIG_DFL`; otherwise profiling is
disabled. This is closer to OpenJDK's conflict-avoidance model than to
java-profiler or dd-trace-py's cookie-and-forward model.

Implications for dd-trace-py:

- gperftools confirms another viable Linux `SIGEV_THREAD_ID` design, but it is
  opt-in and assumes signal/timer ownership rather than coexistence.
- It uses libc `timer_create` because it creates a timer for the current thread
  with `CLOCK_THREAD_CPUTIME_ID`; dd-trace-py needs raw syscalls for arbitrary
  target tids discovered from another thread.
- TLS destructors are one way to couple per-thread timer deletion to thread exit.
  dd-trace-py cannot rely only on that model because it also reconciles threads
  discovered from CPython thread-state walks and has fork/shutdown constraints.
- The callback contract reinforces a shared rule: signal-handler callbacks must
  be constrained and cannot freely lock, allocate, or call back into profiler
  control APIs.
- gperftools' conflict check is a reminder that `SIGPROF` sharing is a policy
  decision. dd-trace-py chooses coexistence with origin validation and forwarding
  where possible.

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
