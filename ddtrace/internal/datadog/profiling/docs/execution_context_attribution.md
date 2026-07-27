# Bounded execution attribution for timer-created CPU samples

## Status

This document is a design proposal. It does not describe a committed public
contract.

The immediate goal is to preserve useful task and endpoint labels on
`timer_create` CPU samples without logical task-stack stitching, ContextVar
introspection, Python-object retention, or drain-time task enumeration.

The design deliberately accepts lower attribution coverage on Python versions
where exact task identity is not cheaply available. Missing labels are
preferable to labels from another task or another point in time.

## Summary

A timer-created CPU sample should contain:

- the physical stack captured when `SIGPROF` was delivered;
- task ID and task name when the interrupted native asyncio task matches a
  previously published bounded attribution entry;
- span ID, local-root span ID, and optional span type from the same entry;
- signal-time per-thread span attribution for non-async threads;
- no logical coroutine ancestry reconstructed from a later task snapshot.

The proposed fast path is:

```text
normal context activation with the GIL:
    determine current native task, if any
    copy task and trace metadata into a fixed-size native cache

SIGPROF:
    identify and validate the current native task
    perform one bounded lock-free cache lookup
    copy validated scalar and bounded-string metadata into RawSample

ring drain:
    render only metadata already copied into RawSample
```

The cache retains no Python references. It has fixed capacity and fixed lookup
work. Cache miss, collision, eviction, transition ambiguity, or validation
failure produces an unlabelled physical CPU stack.

Initial version behavior is:

```text
CPython 3.14 native asyncio task:
    bounded task-keyed attribution

CPython 3.12 and 3.13 asyncio task:
    physical CPU stack without task or endpoint attribution

non-async thread on supported versions:
    bounded per-thread signal-time attribution
```

This is a pragmatic best-effort design. Without retaining task objects or
placing an incarnation token inside signal-visible task state, it cannot
mathematically eliminate every object-address reuse case. The residual risk is
explicitly bounded and documented below.

## Motivation

### Drain-time span and endpoint race

The CPU timer handler captures a physical stack into a per-thread ring. The
sampler drains the ring later, normally within 10 ms. Today,
`render_cpu_sample_begin()` asks `ThreadSpanLinks` for the thread's current span
while draining.

A possible sequence is:

```text
task A, endpoint A: SIGPROF captures A's stack
task A yields
task B, endpoint B: context becomes active
sampler drains A's ring entry
renderer reads B's ThreadSpanLinks entry
```

The sample combines A's physical stack with B's local-root span ID. Filtering by
endpoint B then shows unrelated stacks from A.

This matches the symptom reported in PR #18724:

<https://github.com/DataDog/dd-trace-py/pull/18724#discussion_r3655908901>

Timer samples must not query mutable thread attribution at drain time.

### Drain-time task-stack race

A task can also yield, advance, complete, or be replaced before ring draining.
Reconstructing logical coroutine frames from a later task snapshot can create a
call path that never existed.

The physical stack captured by the signal is authoritative. This design removes
logical task-stack stitching from the timer path.

### Thread identity is insufficient for asyncio

One event-loop thread can execute many task-local tracing contexts. A single
thread-level `ThreadSpanLinks` entry cannot distinguish them.

A useful low-cost compromise is to publish copied attribution per task when the
existing context activation callback executes, then select that entry from the
signal-time current task identity.

## Goals

1. Preserve useful task and endpoint labels on Python 3.14 native asyncio
   workloads.
2. Capture trace attribution at signal delivery rather than ring draining.
3. Avoid full task enumeration or logical task unwinding per CPU sample.
4. Retain no Python task, coroutine, context, name, span, or string references.
5. Use fixed-capacity storage and bounded signal-handler work.
6. Preserve signal-handler constraints: no allocation, locks, Python calls,
   GIL acquisition, or reference-count operations.
7. Omit attribution on every cache or validation failure.
8. Keep the design independent from complete replacement of `ThreadSpanLinks`
   on main.

## Non-goals

- Reconstruct suspended coroutine or parent-task frames for timer CPU samples.
- Provide asyncio timer attribution on Python 3.12 or 3.13 in the first version.
- Identify pure-Python tasks or custom schedulers that do not update CPython's
  native running-task field.
- Guarantee attribution for a child task that only inherited a context and
  never published its own activation.
- Eliminate every theoretical pointer ABA without task retention or an
  incarnation token.
- Replace the application's task factory.
- Replace `ThreadSpanLinks` for all wall, greenlet, thread-pool, and custom
  provider samples as part of this PR.
- Support timer-created CPU profiling on free-threaded CPython.

## Correctness hierarchy

The timer renderer follows this order:

```text
valid task cache entry:
    physical stack + validated task and trace labels

valid non-async thread snapshot:
    physical stack + validated trace labels

anything else:
    physical stack only
```

It never falls back to:

- the task active at drain time;
- the first enumerated task;
- code-location overlap alone;
- the span active at drain time;
- the last thread-level span for an event-loop thread.

## Attribution publication

### Existing activation callback

The profiler already listens to `ddtrace.context_provider.activate`. The
callback currently copies span metadata into `ThreadSpanLinks`.

The timer attribution publisher extends this normal, GIL-owning path. It does
not modify `_DD_CONTEXTVAR` or inspect its internal HAMT.

For a `Span` or propagated `Context`, publication obtains:

```text
span ID
local-root span ID
span type
```

For activation of `None` or a value without usable profiler linkage,
publication invalidates the current attribution rather than leaving the
previous value active.

Clearing `ThreadSpanLinks` on `None` is also an independent main-branch
correctness fix.

### Task publication on Python 3.14

While the callback runs on Python 3.14, native code guardedly reads the current
`_PyThreadStateImpl::asyncio_running_task`.

If a supported native task is current, publication copies:

```text
task object address
task coroutine object address
task context object address
task-name object address
bounded rendered task name
span ID
local-root span ID
bounded span type, if retained
```

The cache stores addresses only as non-owning identity fingerprints. It never
increments their reference counts.

If no supported native task is current, publication updates a per-thread
snapshot only when the thread is not registered as an asyncio event-loop
thread.

### Python 3.12 and 3.13

These versions do not expose the current task directly in `PyThreadState`.
Publication may continue updating ordinary thread attribution, but timer samples
from a registered asyncio event-loop thread do not consume that thread-level
snapshot.

This intentionally omits async task and endpoint labels rather than performing
an O(number of tasks) drain-time search.

### Inherited task contexts

A newly created task can inherit an active trace without calling the activation
callback in that task. Such a task has no task-keyed cache entry and receives no
labels until it publishes an activation itself.

A later task-creation hook could prepopulate inherited attribution, but it is not
required for the first implementation. Missing coverage is safer than assuming
that a parent task's cache entry belongs to the child.

### Task names

Task names are converted and copied while the publisher owns the GIL. The cache
also records the source `task_name` object address.

At signal delivery, the current task's name-object address must still match the
cached address before the copied name is used. If the task was renamed, task ID
and trace labels may remain valid while task name is omitted.

Default numeric task names and Unicode names need bounded conversion rules.
Names longer than the configured inline capacity are truncated with an explicit
marker or omitted. The first implementation should prefer omission if a marker
would violate the rendered-location or profile-label contract.

## Fixed-size task attribution cache

### Entry contents

A conceptual entry is:

```cpp
struct TaskAttributionEntry {
    std::atomic<uint64_t> sequence;
    std::atomic<uintptr_t> task;
    std::atomic<uintptr_t> coroutine;
    std::atomic<uintptr_t> context;
    std::atomic<uintptr_t> task_name_object;
    std::atomic<uint64_t> span_id;
    std::atomic<uint64_t> local_root_span_id;
    std::atomic<uint8_t> task_name_length;
    std::array<std::atomic<uint8_t>, kMaxTaskNameLength> task_name;
    std::atomic<uint8_t> span_type_length;
    std::array<std::atomic<uint8_t>, kMaxSpanTypeLength> span_type;
};
```

Every field concurrently accessed by the signal handler is atomic. A seqlock
around ordinary non-atomic fields would still be a C++ data race and is not
acceptable.

Compile-time assertions must require every handler-visible atomic type to be
always lock-free on enabled platforms.

Span type can be omitted initially. Endpoint labeling needs the local-root span
ID, not span type.

### Capacity and lookup

The table is fixed-size and set-associative. A task address hashes to a fixed
number of candidate entries. The signal handler examines only those entries.

Normal writers may use a mutex to choose an entry, but publication into the
entry follows a sequence protocol:

```text
sequence becomes odd
publish all atomic fields
sequence becomes next even value
```

The signal reader performs one attempt:

```text
read sequence_before
if odd: miss
read all fields
read sequence_after
if changed or odd: miss
validate entry key and task fingerprints
```

The handler does not spin. If it interrupts the writer on the same thread, the
writer cannot progress until the handler returns, so retrying would be useless.

Collision, full set, eviction, or unstable publication is an attribution miss.

### No reclamation

Entries occupy process-lifetime native storage or engine-lifetime storage that
is released only after timers are disarmed and all handlers are quiescent.

Overwriting an entry does not dereference its old Python addresses. No task or
span lifetime is coupled to cache lifetime.

### Fork

A child can inherit the writer mutex while another parent thread held it.
Post-fork reset reconstructs the mutex and invalidates the fixed table without
dereferencing any cached address.

Timer rearming occurs only after cache reset and current-thread registration.

## Per-thread attribution snapshot

Each timer `CaptureState` can contain a small atomic trace-attribution snapshot
for synchronous, non-event-loop execution:

```cpp
struct ThreadAttribution {
    std::atomic<uint64_t> sequence;
    std::atomic<uint64_t> span_id;
    std::atomic<uint64_t> local_root_span_id;
    std::atomic<bool> valid;
};
```

The activation callback updates the snapshot for the current registered native
thread. The signal handler copies it with the same one-attempt sequence
validation used by the task cache.

The thread snapshot is never consumed when the thread is registered as an
asyncio event-loop thread. This avoids assigning one task's latest activation to
another task or to an event-loop callback.

If activation occurs before timer registration, no snapshot is created
retroactively from `ThreadSpanLinks`. Attribution begins after the next
activation.

## Signal-time task validation

### Current task

On Python 3.14, the signal handler reads
`_PyThreadStateImpl::asyncio_running_task`. The field is a strong reference while
the native task is entered, but task transitions update running-task and context
state in separate steps.

The task pointer alone is not sufficient.

### Required checks

The handler guardedly reads the current task's:

```text
task_coro
task_context
task_name
```

It accepts a cache entry only when:

```text
entry.task == asyncio_running_task
entry.coroutine == current task_coro
entry.context == current task_context
current task_context == tstate.context
entry publication is stable
```

The handler should additionally require a signal-captured generator/coroutine
fingerprint belonging to the cached task coroutine before emitting task labels.
This rejects eager-transition windows where the running-task pointer changed but
the new task's coroutine has not begun executing.

If the root task coroutine does not reliably appear in the bounded physical
fingerprint set, this check may reduce coverage. It must not be replaced by a
code-location match or drain-time task search.

Task name is added only when:

```text
entry.task_name_object == current task_name
```

Failure of the name check omits only the name. Failure of task, coroutine,
context, or physical-coroutine validation omits all task-keyed attribution.

### Null or unsupported task

A null `asyncio_running_task` does not prove that no task is executing. Custom
or pure-Python tasks may not update the native field.

On a registered asyncio thread, null or unsupported task identity means no task
or trace labels. The per-thread snapshot is not used as a fallback.

## Signal-time trace attribution

A validated task entry provides the span and local-root IDs associated with the
most recent activation published by that task.

For non-async threads, a validated `CaptureState` snapshot provides those IDs.

The handler copies scalar and bounded-string values into `RawSample`. The ring
never contains task, coroutine, context, name-object, span, or cache-entry
pointers that must be dereferenced while draining.

A conceptual raw value is:

```cpp
struct RawAttribution {
    uint64_t task_id;
    uint64_t span_id;
    uint64_t local_root_span_id;
    uint32_t valid_fields;
    uint8_t task_name_length;
    char task_name[kMaxTaskNameLength];
};
```

The exact layout must remain trivially copyable and preserve the SPSC ring's
allocation-free behavior.

## Drain behavior

`render_cpu_sample_begin()` receives the copied raw attribution. It does not
call `ThreadSpanLinks`.

The timer drain path does not call:

- `get_all_tasks()`;
- `unwind_selected_task()`;
- `matching_active_fingerprint()` for a drain-time task;
- `stitch_captured_stack()`;
- any task-name or span lookup using mutable Python state.

Rendering behavior is:

```text
always:
    CPU time
    thread labels
    physical frames

when raw task fields are valid:
    task ID
    task name, if separately valid

when raw trace fields are valid:
    span ID
    local-root span ID
    span type, if retained
```

## Residual limitations

### Pointer ABA

The cache retains no references. A completed task, its coroutine, and its
context can be freed and their addresses reused.

The signal handler compares several independently sourced addresses:

```text
task
coroutine
context
task-name object, for name only
physical coroutine fingerprint
```

Reusing all relevant identities before an old cache entry is evicted is much
less likely than reusing the task address alone, but it is not impossible. This
is the primary correctness trade-off made to avoid strong references and a task
incarnation registry.

The design must be described as pragmatic best effort. If strict incarnation
proof becomes a requirement, task labels must be omitted until a signal-visible
incarnation token exists.

### Task-name freshness

A rename changes the task-name object in normal implementations and causes name
validation to fail. An address ABA could theoretically make a later name object
match an old address. This has the same residual limitation as task identity.

### Activation coverage

Attribution represents the latest profiler activation published by the task.
Tasks that only inherit context and never activate do not have entries. Cache
eviction also reduces coverage.

### Span metadata mutation

Span ID, local-root span ID, and span type are copied at activation. Normal
tracer behavior must be audited to ensure profiler-relevant fields do not change
without another activation. If they do, the corresponding mutation needs a
republish hook or timer attribution must be omitted for that state.

### Custom providers and tasks

A provider that does not dispatch the profiler activation event cannot publish
an entry. A task implementation that does not expose the native CPython task
layout cannot be validated by the signal handler.

Both cases produce unlabelled timer samples.

## Python-version behavior

### CPython 3.14

Supported native asyncio tasks can use the task cache and signal-time validation.
The implementation must audit exact task and thread-state layouts for each
enabled patch version and architecture.

### CPython 3.12 and 3.13

Asyncio timer samples contain physical frames and CPU time without task or trace
attribution. Synchronous non-event-loop threads can use per-thread attribution.

### Free-threaded CPython

Timer-created CPU profiling remains disabled. This design does not make its
private reads safe for free-threaded builds.

## Relationship to `ThreadSpanLinks` on main

This timer design does not require replacing `ThreadSpanLinks` globally.

### Independent fixes

Two fixes are useful regardless of timer attribution:

1. Activation of `None` or unusable span metadata must clear the current
   `ThreadSpanLinks` entry.
2. Timer drain must not read `ThreadSpanLinks`.

The task-unsafe propagated-root `threading.local()` also needs separate review.

### Potential reuse for wall task samples

Wall sampling already discovers individual `TaskObj` values. A task-keyed cache
could eventually provide each task's last published span attribution instead of
assigning one thread-level span to every task.

This would still be best effort:

- wall sampling reads another live thread;
- the task can run, mutate, complete, or be reused during capture;
- tasks without an activation entry remain unlabelled;
- Python 3.12 and 3.13 publication would need a cheap current-task lookup under
  the GIL, likely outside the native timer fast path.

The cache can supplement or gradually replace `ThreadSpanLinks` for task samples,
but that migration is not part of the timer PR.

### Non-async wall samples

`ThreadSpanLinks` can remain the existing best-effort mechanism for ordinary
wall samples while timer signals use `CaptureState` snapshots. A future shared
atomic thread snapshot may replace the map, but remote stack/context coherence
needs separate analysis.

### Greenlets, thread pools, and custom providers

- `OriginTaskLinks` for thread-pool submission is a separate relationship and
  remains unchanged.
- Greenlet samples need greenlet-specific identity and context publication.
- Custom providers need an explicit publication contract.

Complete `ThreadSpanLinks` removal is therefore a possible follow-up, not a goal
of this design.

## Safety requirements

The signal handler may perform only:

- fault-guarded reads of the interrupted thread's audited CPython state;
- bounded scans of fixed cache sets and bounded physical fingerprints;
- lock-free atomic loads and stores;
- copies into a reserved `RawSample` slot.

It must not:

- allocate or free;
- acquire a mutex or the GIL;
- call Python or generic dictionary/ContextVar APIs;
- modify reference counts;
- access an ordinary `unordered_map`;
- spin waiting for a writer;
- dereference cached Python addresses after signal delivery.

Every handler-visible atomic type must be verified as always lock-free.

## Performance model

At the default 10 ms interval, a continuously busy event-loop thread can produce
about 100 CPU samples per second.

The design target is:

```text
activation:
    one bounded cache publication

signal:
    a few guarded task-field reads
    one fixed-set cache lookup
    one bounded coroutine-identity validation
    scalar and bounded-string copy

drain:
    O(1) attribution rendering
```

It avoids:

```text
100 samples/second * number of live asyncio tasks
```

Benchmark:

- tracing activation with cache disabled and enabled;
- task cache hit, miss, collision, eviction, and interrupted publication;
- signal duration with and without task validation;
- task-name conversion and copy cost;
- cache memory at candidate capacities;
- additional bytes per `RawSample` and per 64-slot ring;
- 10, 1,000, and 10,000 mostly sleeping tasks;
- task churn designed to force address reuse;
- synchronous workloads using per-thread attribution.

## Correctness tests

### Endpoint switch before drain

```text
task A under endpoint A consumes CPU
SIGPROF captures A
task A yields
task B under endpoint B becomes current
ring drains
```

The sample contains A's labels or no labels. It never contains B's labels.

### Fresh task replacement

Task A completes after signal capture. Task B is allocated before drain. B's
name or endpoint must not be read while draining.

### Task transition windows

Deliver signals throughout Python 3.14 native and eager task enter/leave paths.
No task entry is accepted until task, coroutine, context, and physical
coroutine-identity checks all pass.

Include two tasks intentionally sharing one explicit `contextvars.Context`.

### Task rename

Publish task name A, rename to B, and signal before another trace activation.
The sample may omit the name but must not report A as the current name.

### Activation of `None`

After deactivation, both task cache and per-thread snapshot are invalid. Later
untraced CPU work receives no previous endpoint.

### Inherited context without activation

A child task inherits a parent span but never publishes its own activation. Its
timer samples remain unlabelled rather than borrowing the parent's task entry.

### Cache publication interruption

Deliver a signal after the writer makes the sequence odd but before publication
finishes. The handler performs one attempt, observes instability, and omits
attribution without deadlock.

### Cache collision and eviction

Force all ways in one set to be occupied and evicted. Every old or unstable entry
produces a miss or passes all current-task identity checks.

### Pointer reuse stress

Rapidly create and destroy tasks, coroutines, contexts, and names. Track whether
all cached identity addresses are reused together and quantify the residual ABA
risk.

### Python 3.12 and 3.13

On an asyncio event-loop thread, confirm that timer samples do not consume the
per-thread attribution snapshot. On synchronous threads, confirm that the
signal-time thread snapshot is rendered correctly.

### Fork and shutdown

- fork while another thread publishes an entry;
- child cache reset without dereferencing inherited addresses;
- timer rearm only after reset;
- cache destruction only after handler quiescence;
- no cached Python references retained at shutdown.

## Telemetry

Add counters for:

- task cache publication;
- task cache hit and miss;
- cache collision and eviction;
- unstable sequence read;
- task, coroutine, context, name, and physical-fingerprint mismatch;
- async attribution unsupported by Python version;
- thread snapshot hit and miss;
- timer samples with task ID;
- timer samples with task name;
- timer samples with span and local-root IDs.

Canary evaluation should use CPU-time-weighted attribution coverage and measure:

- whether endpoint filters still contain unrelated physical stacks;
- what fraction of Python 3.14 timer CPU time receives task and endpoint labels;
- how much coverage is lost to inherited contexts and cache eviction;
- handler and activation overhead.

## Staged plan

### Stage 0: correctness simplification

1. Stop reading `ThreadSpanLinks` when draining timer samples.
2. Remove timer logical task-stack stitching and per-sample task enumeration.
3. Clear `ThreadSpanLinks` on activation of `None`.
4. Render physical timer stacks without task or endpoint labels until the
   bounded attribution path is available.

### Stage 1: synchronous signal-time attribution

1. Add atomic attribution storage to `CaptureState`.
2. Publish and clear it from the existing activation callback.
3. Mark asyncio event-loop threads and prohibit thread-snapshot fallback there.
4. Copy attribution into `RawSample` in the signal handler.
5. Validate synchronous endpoint switching and overhead.

### Stage 2: Python 3.14 task cache

1. Add the fixed-size set-associative cache.
2. Publish task identity, copied task name, and trace metadata under the GIL.
3. Add task/coroutine/context/name validation in the signal handler.
4. Require a captured coroutine identity before emitting task-keyed labels.
5. Copy validated metadata into `RawSample`.
6. Canary cache hit rate, ABA stress, endpoint isolation, and overhead.

### Stage 3: optional coverage improvements

1. Evaluate prepopulation for inherited child tasks.
2. Evaluate a true task incarnation token if pointer reuse is measurable.
3. Evaluate Python 3.12 and 3.13 task publication only if it avoids drain-time
   enumeration.
4. Evaluate task-name rename hooks only if name omissions are material.

### Stage 4: optional main reuse

1. Evaluate the cache for ordinary wall task samples.
2. Keep remote wall semantics explicitly best effort until separately proven.
3. Migrate no `ThreadSpanLinks` consumer merely because the timer cache exists.
4. Consider global removal only after greenlet, thread-pool, custom-provider,
   fork, and remote-capture behavior have explicit replacements.

## Open questions

1. What fixed cache capacity and associativity provide useful hit rates without
   excessive process memory?
2. Does the task's root coroutine reliably appear in the bounded physical
   fingerprint set during ordinary Python and native-extension CPU work?
3. Should task name and span type be omitted from the first cache version to
   reduce entry and ring size?
4. Is the residual multi-pointer ABA risk acceptable for a private canary
   feature, or must task labels wait for a true incarnation token?
5. How should inherited child tasks be registered without replacing task
   factories or retaining task references?
6. Can the same activation publisher cheaply identify the current task on
   Python 3.12 and 3.13 outside signal context?
7. Can a shared atomic thread snapshot eventually replace the map-based
   `ThreadSpanLinks` for synchronous main sampling?
8. Which existing span or propagated-context mutations require attribution
   republishing?
