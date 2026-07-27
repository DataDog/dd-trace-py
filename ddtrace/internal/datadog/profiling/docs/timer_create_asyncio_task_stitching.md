# timer_create asyncio task attribution

## Problem

The `timer_create` CPU profiler can produce an impossible asyncio call stack by
combining physical Python frames captured when a timer signal arrives with
logical task frames observed later when the sampler drains the CPU sample ring.

A profile from the
[`python-cpu-accuracy` timer-create workload](https://app.datadoghq.com/profiling/profile/AZ-Tmsk9AADTQF26N3RRygAA?query=service%3Apython-cpu-accuracy%20ddtrace_variant%3Atimer_create&agg_m=count&agg_m_source=base&agg_t=count&event=AwAAAZ-TmskAsJYeKwAAABhBWi1UbXNrOUFBRFRRRjI2TjNSUnlnQUEAAAAkZjE5ZjkzOWMtMzFmZS00NjU5LWE5MTktMGYxMjJhNDI0ZTUwAASvjA&eventScope=profile&fromUser=true&my_code=disabled&refresh_mode=paused&viz=stream&from_ts=1784571814342&to_ts=1785176614342&live=false)
contains this caller-to-callee CPU stack on CPython 3.14.6. The profile used
`ddtrace==4.13.0rc1`, built from
`taegyunkim/prof-14213-timer-create@742b17428ee72f724784480909ea56c9c803738c`:

```text
worker.py:handle_request_async:31
cpusimulator.py:CPUSimulator.simulate_off_cpu_async:72
asyncio/tasks.py:sleep:704
worker.py:handle_request_async:32
cpusimulator.py:CPUSimulator.cpu_loop:15
<native>:cpu_loop_impl._cpu_loop
```

The corresponding source executes the sleep and CPU work sequentially:

```python
async def handle_request_async(sim, loops_num, off_cpu, stats):
    await sim.simulate_off_cpu_async(off_cpu)  # line 31
    sim.cpu_loop(loops_num)                    # line 32
    stats.requests_completed += 1
```

`cpu_loop()` can execute only after `asyncio.sleep()` has returned. The sleep
frame and the line-32 CPU frames therefore cannot coexist in a real Python
stack. The two `handle_request_async` frames at mutually exclusive source
locations make the temporal mixing explicit.

A query over the profile link's time range found this exact impossible stack
accounting for approximately 3.8 CPU seconds per minute, about 15% of the total
CPU in that scope. This is a material correctness problem rather than a rare
rendering artifact. The wall-time profile shows separate sleep and CPU branches
and does not contain the impossible chain.

The CPU-time weight remains valid. The incorrect portion is the reconstructed
asyncio ancestry attached after the physical CPU stack was captured.

## Root cause

A per-native-thread CPU timer sends `SIGPROF` while the thread consumes CPU.
`cpu_timer_signal_handler()` in `stack/src/cpu_timer.cpp` captures raw physical
Python frames and publishes them to a per-thread ring buffer. It does not unwind
asyncio task state in the signal handler.

Later, the sampler thread drains the ring and calls:

```cpp
ThreadInfo::sample_cpu_timer(echion, tstate, captured_stack, cpu_time_us)
```

`sample_cpu_timer()` stores the captured stack in `python_stack`, calls
`unwind_tasks()` using a fresh task snapshot, and then calls:

```cpp
mark_cpu_timer_task_stack(timer_stack, current_tasks);
```

The current selection logic:

1. selects the first task marked `on_cpu` at drain time, without first proving
   that it produced the captured sample;
2. otherwise selects the first task whose reconstructed stack shares a
   `Frame::Key` with the captured physical stack;
3. leaves the selection at `current_tasks[0]` if neither search finds a match;
4. combines the selected drain-time task stack with the captured physical stack.

The overlap check does not establish task identity. `Frame::Key` identifies a
code location, so two task incarnations executing the same function at the same
bytecode location can match. `unwind_tasks()` also incorporates portions of the
captured `python_stack` while constructing task stacks, so those task stacks are
not independent snapshots against which overlap can be proven.

The accuracy workload makes this race frequent. Each long-lived slot repeatedly
creates a fresh request task:

```python
while time.monotonic() < deadline:
    await asyncio.create_task(handle_request_async(...))
```

A representative sequence is:

1. request task N consumes CPU in `cpu_loop()` at line 32;
2. `SIGPROF` captures its physical stack;
3. request task N completes before the ring is drained;
4. the slot starts request task N+1;
5. request task N+1 reaches `asyncio.sleep()` at line 31;
6. drain-time task unwinding observes request N+1;
7. overlap at a stable outer location, likely `slot_async:44`, combines request
   N's CPU frames with request N+1's sleeping coroutine frames.

In the internal leaf-to-root representation, the merger effectively combines:

```text
signal-time prefix:
    cpu_loop_impl._cpu_loop
    CPUSimulator.cpu_loop
    handle_request_async:32

drain-time logical suffix:
    asyncio.sleep
    simulate_off_cpu_async
    handle_request_async:31
    slot_async:44
```

The pprof viewer reverses this into the impossible caller-to-callee stack shown
above.

## Correctness requirements

The physical stack captured at timer expiry is authoritative. Drain-time
processing may validate, symbolize, or omit invalid captured frames, but it
must not replace, reorder, or discard valid captured physical frames in favor
of a later task snapshot.

Best-effort logical ancestry may be appended only when two separate conditions
hold:

1. signal-time object identity selects exactly one live task; and
2. the selected task's drain-time active coroutine still matches a
   signal-time coroutine fingerprint.

Any correction must preserve the timer signal handler's existing properties:

- no allocation;
- no locks;
- no GIL acquisition;
- no Python calls;
- no reference-count operations;
- only fault-guarded reads of target interpreter memory;
- fixed, preallocated sample storage.

Task attribution must follow these rules:

- use a task or coroutine object address captured at signal time to select the
  only task eligible for unwinding;
- do not use code location, function name, line number, or bytecode offset by
  itself as task identity;
- use drain-time `on_cpu` only as a consistency check after identity selection,
  never to choose a task;
- do not fall back to the first enumerated task;
- if identity cannot be established, render an unlabelled physical CPU stack;
- if identity matches but execution consistency does not, attach task metadata
  if desired but render only the captured physical stack.

Losing logical ancestry is preferable to attaching a later task state without
any evidence that it still represents the sampled execution.

## Identity-gated best-effort task stitching

The current CPU-timer use of the following logic must be removed:

```text
select first drain-time on_cpu task
code-location overlap task selection
current_tasks[0] fallback
build candidate task stacks using the captured python_stack
```

The existing `unwind_tasks()` flow cannot be reused unchanged because it
incorporates `python_stack` into task stacks before task identity and snapshot
consistency are established. Add a targeted path that first selects one task by
signal-time identity, then constructs a pure logical stack for only that task.

Conceptually:

```cpp
auto task = find_task_from_signal_time_identity(raw_sample);
if (task == nullptr) {
    render_physical_stack(captured_stack);
} else if (task_snapshot_matches_capture(*task, raw_sample)) {
    auto logical_stack = unwind_selected_task(*task);
    render_stitched_stack(captured_stack, logical_stack, raw_sample);
} else {
    render_task_labels_and_physical_stack(*task, captured_stack);
}
```

`render_stitched_stack()` must retain every valid captured physical frame. It
may append only logical ancestors above the exact coroutine boundary identified
by the signal-time fingerprint. It must not merge at the first shared code
location.

The consistency check should require:

- the selected object is the signal-identified task;
- the task is still marked `on_cpu` in the drain snapshot;
- the active generator or coroutine object matches a signal-time object
  address;
- its code object, bytecode offset, and first line still match the captured
  execution point.

A suitable bounded signal-time record is:

```cpp
struct CoroutineFingerprint {
    uintptr_t coroutine;
    uintptr_t code_object;
    int lasti;
    int first_lineno;
};

static constexpr size_t kMaxCoroutineFingerprints = 8;
```

The handler records fingerprints while it already walks generator-owned
physical frames. The fixed bound preserves signal safety. Eight fingerprints
plus the task address increase `RawSample` from 8,224 to 8,424 bytes on the
64-bit Linux build, increasing the 64-slot ring by 12,800 bytes per registered
thread, approximately 2.4%. If the relevant boundary is not retained,
consistency cannot be established and the sample falls back to task labels plus
its physical stack.

## Signal-time task identity on Python 3.14

CPython 3.14 stores the current native asyncio task in the interrupted thread
state:

```cpp
_PyThreadStateImpl::asyncio_running_task
```

CPython 3.14.0 sets this strong reference in `enter_task()` before executing a
native asyncio task and clears it in `leave_task()` when the task yields or
returns. `asyncio.current_task()` uses the same field as its current-thread fast
path. A non-null value is therefore the native task entered on the thread at
signal delivery, not merely a task associated with that thread. Because
`SIGPROF` interrupts the same native thread, the handler can fault-guardedly
read the field and save its address as a non-owning identity in the preallocated
raw sample:

```cpp
struct RawSample {
    uint64_t cpu_delta_ns;
    uint64_t python_thread_id;
    uint64_t native_tid;
    uintptr_t asyncio_task;
    std::array<CoroutineFingerprint, kMaxCoroutineFingerprints> coroutine_fingerprints;
    uint8_t coroutine_fingerprint_count;
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

At drain time, compare live task object addresses with `raw.asyncio_task`. Only
that exact task is eligible for unwinding. If it remains `on_cpu` and its active
coroutine matches a captured fingerprint, its current logical ancestors may be
stitched above the physical stack.

If the task is live but sleeping or its active coroutine fingerprint changed,
attach only its task ID and optional name. If the task completed before
draining, it will not appear in the fresh task enumeration, so render an
unlabelled physical stack.

This prevents the observed accuracy-workload failure. The signal identifies
request task N, while the drain snapshot contains sleeping request task N+1.
Task N+1 has a different object address and is never eligible for unwinding.

A null `asyncio_running_task` does not prove that no task is executing. A custom
or pure-Python task implementation can maintain asyncio's module-level current
task dictionary without updating the native thread-state field. In that case,
the bounded coroutine fingerprints provide the fallback identity mechanism.
Also, if `SIGPROF` was blocked, the field describes the task at delayed signal
delivery while the accumulated CPU delta may include earlier work. The physical
stack has the same delivery-time semantics.

## Signal-time task identity on Python 3.12 and 3.13

CPython 3.12.13 and 3.13.14 do not expose the current task directly in
`PyThreadState`. They maintain the current-task association in an asyncio module
state dictionary mapping event loops to tasks. Looking up that dictionary from
the signal handler is unsafe because it requires Python dictionary access,
hashing, reference-count operations, and, on some builds, locking.

The physical interpreter frames provide a safer identity source. In these
versions, a coroutine-owned frame has:

```cpp
frame->owner == FRAME_OWNED_BY_GENERATOR
```

The enclosing generator or coroutine object's address can be derived from the
embedded interpreter-frame address using checked integer arithmetic equivalent
to:

```cpp
frame_address - offsetof(PyGenObject, gi_iframe)
```

Record that address together with the frame's code object, bytecode offset, and
first line as a `CoroutineFingerprint`. At drain time, compare the captured
coroutine addresses against `GenInfo::origin` in each live task's coroutine and
await chain. Select a task only when exactly one task contains a captured
coroutine object:

```text
one matching task       -> eligible for consistency validation and unwinding
zero matching tasks     -> render an unlabelled physical stack
multiple matching tasks -> render an unlabelled physical stack
```

After selecting a unique task, apply the same `on_cpu` and active-coroutine
fingerprint checks used on Python 3.14. A consistent snapshot permits
best-effort logical stitching. An inconsistent snapshot permits task metadata
only, followed by the captured physical stack.

A bounded fingerprint list may omit the useful task boundary for an unusually
deep coroutine chain. That causes lost ancestry rather than guessed ancestry.
The exact bound and retention policy must be validated against ordinary
coroutine chains, eager task execution, and deeply nested awaits.

## Pointer reuse and task incarnation IDs

A captured object address can theoretically be reused if the original task or
coroutine completes, is freed, and another object is allocated at the same
address before the ring entry is drained.

The profiler already uses task object addresses as numeric task IDs, so emitting
the signal-captured address as a historical ID follows existing semantics. The
risk appears when a reused address is matched to a new live object and its name
or other metadata is attached to the old sample.

A fully robust implementation can maintain a registry outside the signal
handler:

```text
object address -> monotonically increasing task incarnation ID
```

The signal handler would obtain an immutable `(address, incarnation)` record
through a lock-free lookup, and the drain side would match both values. This is
more complex and should follow only if pointer reuse is shown to be material or
if the registry is needed for other task-correlation work. Until then, failure
to find a unique live identity match must remain unlabelled.

## Best-effort consistency limitations

Task identity and a coroutine fingerprint make drain-time stitching much safer,
but they are not a formal historical snapshot. A long-lived task can execute a
loop and return to the same coroutine and bytecode offset before the ring is
drained. This address-and-location ABA case can satisfy the fingerprint even
though execution advanced in between.

Eliminating that residual case requires stronger signal-time state, such as:

- a task execution epoch incremented on every enter, leave, or suspension;
- a task incarnation registry combined with an execution epoch; or
- direct signal-time capture of the logical coroutine ancestry into bounded
  preallocated storage.

The initial implementation treats identity plus the active coroutine
fingerprint as best effort. The unconditional safeguards remain:

- never select a different task based on code overlap;
- never stitch a task that is sleeping at drain time;
- never discard valid captured physical frames;
- fall back to less ancestry whenever consistency is uncertain.

## Version behavior

- Python 3.14 and later should use `asyncio_running_task` as the primary
  signal-time task identity and coroutine fingerprints for snapshot
  consistency.
- Python 3.12 and 3.13 should use bounded coroutine fingerprints for both unique
  task selection and snapshot consistency.
- Every version may append best-effort logical ancestors only after identity
  and consistency validation.
- If identity matches but consistency fails, render task metadata with the
  signal-captured physical stack.
- If no unique identity match is available, render an unlabelled physical
  stack.
- The timer-create CPU profiler is disabled for free-threaded CPython builds,
  so this design does not need to make these private reads safe for that
  configuration.

## Regression coverage

Add an integration regression that repeatedly creates a fresh request task:

```python
async def handle_request():
    await asyncio.sleep(0.02)
    cpu_burst(0.002)

async def slot(deadline):
    while loop.time() < deadline:
        await asyncio.create_task(handle_request())
```

Use a timer interval shorter than the 10 ms ring-drain cadence so some samples
are captured during the CPU burst and drained after the task has transitioned
or completed. Inspect emitted CPU-time samples and assert that no stack
contains both:

```text
asyncio.tasks.sleep
cpu_burst
```

Also assert that a stack does not contain `handle_request` at both the sleep
line and CPU line.

The regression must fail on the overlap-based implementation and pass when the
signal-identified request task is the only task eligible for unwinding.

Add a second regression using one long-lived task that alternates between CPU
work and sleep. This keeps the task object address constant while changing its
coroutine state, proving that task identity alone is insufficient. A sample
captured in CPU work and drained while that task sleeps must retain its physical
stack without appending the sleep ancestry.

Keep a positive stitching test in which one named task remains on CPU across
multiple drain cycles. It should prove that:

- Python 3.14 task-pointer matching preserves the task label;
- Python 3.12 and 3.13 coroutine-identity matching preserves the task label;
- a consistent fingerprint preserves expected logical ancestry;
- all captured physical frames remain present after stitching.

Additional coverage should include:

- a task that completes before its sample is drained;
- multiple tasks executing the same code location;
- the same task progressing to a different bytecode location before drain;
- nested coroutine chains;
- eager task execution;
- fingerprint-list overflow and safe fallback;
- standard asyncio and uvloop.

## Related delayed-context issues

Correcting task attribution does not correct other metadata that is currently
read during ring draining.

`StackRenderer::render_cpu_sample_begin()` looks up the active span by thread ID
at drain time. If the task or request changed after `SIGPROF`, a physical stack
from task A can receive task B's span and endpoint labels. Signal-time span and
local-root IDs should be captured through lock-free per-thread state, or timer
samples should omit span metadata when it cannot be captured safely.

Greenlet stitching has the same temporal constraint. A greenlet can switch
after signal capture and before draining. Drain-time greenlet frames must not be
attached to a timer sample without a corresponding signal-time greenlet
identity.
