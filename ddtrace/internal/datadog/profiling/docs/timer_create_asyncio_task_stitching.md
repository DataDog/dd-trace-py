# timer_create asyncio task stitching

## Problem

The `timer_create` CPU profiler can produce an impossible asyncio call stack in
which a CPU-time sample captured in synchronous work is rendered underneath a
later await point. For example:

```text
handle_request_async
  simulate_off_cpu_async
    asyncio.sleep
      CPUSimulator.cpu_loop
```

In the workload below, `cpu_loop()` executes only after
`simulate_off_cpu_async()` returns, so these frames cannot coexist in a real
Python stack:

```python
async def handle_request_async(sim, loops_num, off_cpu, stats):
    await sim.simulate_off_cpu_async(off_cpu)
    sim.cpu_loop(loops_num)
    stats.requests_completed += 1
```

The issue was observed with `ddtrace==4.13.0rc1`, built from
`taegyunkim/prof-14213-timer-create@742b17428ee72f724784480909ea56c9c803738c`,
on a Python 3.14 asyncio workload.

The CPU-time weight remains valid. The incorrect portion is the reconstructed
asyncio ancestry attached after the physical CPU stack was captured.

## Root cause

A per-native-thread CPU timer sends `SIGPROF` while the thread consumes CPU.
`cpu_timer_signal_handler()` in `stack/src/cpu_timer.cpp` captures raw physical
Python frames in the signal handler and publishes them to a per-thread ring
buffer. It does not unwind asyncio task state in the handler.

Later, the sampler thread drains the ring and calls:

```cpp
ThreadInfo::sample_cpu_timer(echion, tstate, captured_stack, cpu_time_us)
```

In `stack/src/echion/threads.cc`, `sample_cpu_timer()` calls `unwind_tasks()`
to inspect the asyncio task state at drain time, then invokes:

```cpp
mark_cpu_timer_task_stack(timer_stack, current_tasks);
```

The task may have yielded between signal delivery and ring-buffer draining. In
the reproducer, the task can have completed `cpu_loop()` and started its next
`await asyncio.sleep()` by the time `unwind_tasks()` runs.

`mark_cpu_timer_task_stack()` currently falls back to the first current task
whose logical stack shares any frame with the captured physical stack. It then
merges the two stacks through:

```cpp
merge_captured_stack_with_task_stack(captured_stack, task_stack)
```

A shared `handle_request_async` frame is enough to select the task even though
its current logical leaf is now `asyncio.sleep`. The result is a hybrid stack
that never existed at one instant.

## Requirements

Any correction must preserve the properties of the timer signal handler:

- no allocation;
- no locks;
- no GIL acquisition;
- no Python calls;
- no reference-count operations;
- only fault-guarded reads of target interpreter memory.

The stack captured at timer expiry is the authoritative CPU stack. A task
snapshot taken later must not alter or extend those physical frames.

## Immediate conservative fix

Remove the overlap-only fallback from `mark_cpu_timer_task_stack()`.

A timer sample may be stitched only if exactly one task is both:

1. marked `on_cpu` in the drain-time task snapshot; and
2. consistent with the captured physical stack at a task boundary.

If no unique match is found, render the raw physical CPU stack without asyncio
task stitching. This can lose a task label when the task yielded before the
ring was drained, but it prevents false call paths and preserves correct
CPU-time attribution.

Conceptually:

```cpp
auto task = find_unique_task(current_tasks, [&](const StackInfo& candidate) {
    return candidate.on_cpu && overlaps(candidate.stack, captured_stack);
});

if (task != nullptr) {
    stitch_captured_stack(*task, captured_stack);
} else {
    render_raw_cpu_sample(captured_stack);
}
```

The current behavior of selecting a non-running task solely because it shares
a frame with the captured stack must not be retained.

## Preferred fix: capture task identity at signal time

On CPython 3.14 and later, the current native asyncio task is available in the
interrupted thread state:

```cpp
_PyThreadStateImpl::asyncio_running_task
```

CPython sets this field before executing a native `asyncio.Task` and clears it
when the task yields or returns. Because `SIGPROF` interrupts that same thread,
the handler can fault-guardedly read the field and save its address as a
non-owning identity in the preallocated raw sample:

```cpp
struct RawSample {
    uint64_t cpu_delta_ns;
    uint64_t python_thread_id;
    uint64_t native_tid;
    uintptr_t asyncio_task;
    uint16_t depth;
    std::array<RawFrame, kMaxCpuTimerFrames> frames;
};
```

The handler must not incref or otherwise dereference the task object:

```cpp
PyObject* task = nullptr;
if (tstate != nullptr) {
    auto* ts = reinterpret_cast<_PyThreadStateImpl*>(tstate);
    guarded_read_scalar(*state, task, &ts->asyncio_running_task);
}
sample->asyncio_task = reinterpret_cast<uintptr_t>(task);
```

At drain time, enumerate live tasks as usual and compare their object addresses
with `raw.asyncio_task`. The captured identity may supply the task ID and task
name, but the output frames must remain the original physical captured stack.
Do not append the matched task's current coroutine stack.

```cpp
if (auto task = find_task_by_address(current_tasks, raw.asyncio_task)) {
    render_cpu_sample_for_task(*task, captured_stack);
} else {
    render_raw_cpu_sample(captured_stack);
}
```

The drain-side lookup must compare addresses only. It must never dereference
the saved non-owning pointer. If the task is no longer present in the fresh
task snapshot, render an unlabelled raw sample.

## Pointer reuse and task incarnation IDs

A raw object address can theoretically be reused if a task completes, is freed,
and a new task is allocated at the same address before the ring entry is
drained. This is unlikely with prompt draining but is not a complete identity
guarantee.

A fully robust implementation can maintain a task registry outside the signal
handler:

```text
task object address -> monotonically increasing task incarnation ID
```

Task lifecycle instrumentation assigns the incarnation at registration and
removes it on task completion. The signal handler reads the current task address
and obtains an immutable `(address, incarnation)` record through a lock-free
lookup. The drain side matches both values against the live task registry.

This is more complex than using the CPython task address directly, so it should
follow only if pointer reuse is shown to be material or if the registry is
needed for other task-correlation work.

## Version behavior

- Python 3.14 and later can use `asyncio_running_task` for signal-time native
  asyncio task identity.
- Python 3.12 and 3.13 should use the conservative behavior. Do not stitch a
  timer sample to a later non-running task based only on frame overlap.
- The timer-create CPU profiler is disabled for free-threaded CPython builds,
  so this design does not need to make signal-time task reads safe in that
  configuration.

## Regression coverage

Add a CPU-timer integration test that repeatedly executes:

```python
async def work():
    cpu_loop()
    await asyncio.sleep(0.05)
```

Inspect CPU-time samples in the emitted pprof. Assert that a sample containing
`cpu_loop` does not also contain either `simulate_off_cpu_async` or
`asyncio.sleep`.

The test should exercise the delayed-drain path, where the task reaches the
await after the timer signal captured the CPU stack but before the sampler
consumes the ring entry. It should fail with overlap-only task stitching and
pass when the output uses signal-time task identity or falls back to the raw
physical stack.
