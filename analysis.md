# crash46 -- Follow-up Analysis (fix, CPU attribution, and why it's rare)

Follow-up to `crashes/crash46.md`. Covers: the fix that was applied, why per-greenlet
CPU attribution is not lost, and -- most importantly -- **why this crash is rare rather
than deterministic**, which corrects the original "dereferences an invalid pointer ->
crash" phrasing.

## TL;DR

- Under gevent, `Thread.ident` is `id(greenlet)` (a live Python heap address), not an OS
  `pthread_t`. dd-trace-py passed it to `pthread_getcpuclockid`, which treats it as a
  `struct pthread *`.
- `pthread_getcpuclockid` does **not** crash on most calls. It performs an
  **out-of-bounds read far past the end of the greenlet object**, which almost always
  lands in adjacent mapped heap (returns garbage, no crash). It faults only when that read
  crosses into an unmapped/guard page. That is why the crash is rare and heap-layout
  dependent, even though greenlet usage is common.
- The fix builds the per-thread CPU clock id directly from the kernel TID (`native_id`)
  instead of dereferencing the (bogus) `pthread_t`, so the OOB read never happens.
- No CPU attribution is lost: greenlets have no distinct kernel CPU clock; CPU time is
  measured at the OS-thread level (from `native_id`) and attributed to the on-CPU greenlet
  by the sampler.

## Why this crash is rare (the important part)

`id(greenlet)` is the address of a **live, mapped** Python object. glibc's
`pthread_getcpuclockid` casts that address to `struct pthread *` and reads the `tid` field:

```c
// glibc (x86-64)
*clockid = MAKE_THREAD_CPUCLOCK (pd->tid, CPUCLOCK_SCHED);
```

The `tid` field lives **~0x2d0 (~720) bytes** into `struct pthread` on x86-64 glibc (after
the ~704-byte `tcbhead_t` plus the `list_t` link). A greenlet object is only a few hundred
bytes, so the read lands **hundreds of bytes past the end of the greenlet object**, into
whatever happens to be adjacent on the heap:

- **Common case (no crash):** the adjacent memory is mapped (more heap). The read succeeds
  and returns **garbage**, yielding a bogus `clockid`. Later, `clock_gettime(bogus_clockid)`
  fails with `EINVAL`, which `ThreadInfo::update_cpu_time` already swallows. Result: no
  crash, a silently-wrong (and effectively unused) CPU clock.
- **Rare case (this crash):** the greenlet object sits near the top of a heap arena / next
  to a guard or unmapped page, so `addr + ~0x2d0` falls in a page that is mapped-but-
  protected or unmapped -> `SIGSEGV` with `SEGV_ACCERR`.

`SEGV_ACCERR` (invalid *permissions* for a mapped region), as opposed to `SEGV_MAPERR`
(address not mapped at all), is consistent with an OOB read crossing into a guard/protected
page rather than a wild pointer into nothing.

So the original phrasing "dereferences an invalid pointer -> crash" was too strong: it is an
out-of-bounds read that *usually* hits mapped memory and only occasionally faults. This is
why greenlet-heavy apps run fine the vast majority of the time and only crash sporadically.

## Can per-greenlet CPU time ever come from this code path? No.

`pthread_getcpuclockid` (and the clock id the fix builds) is a **per-kernel-thread** CPU
clock. Greenlets are cooperatively scheduled on top of a single OS thread -- they have no
distinct kernel thread and therefore no distinct kernel CPU clock. Even with a "real" id you
could never get per-greenlet CPU time out of a POSIX CPU clock. And the old code did not
attribute anything anyway -- it either read garbage or segfaulted.

## How greenlet CPU attribution actually works (and why the fix preserves it)

```
ThreadInfo::sample(...):
    update_cpu_time();                              // OS-thread CPU clock
    renderer.render_cpu_time(cpu_time - previous);  // measured at thread level
    unwind(...);
    render_unwound_stacks(...);                     // attributes to on-CPU greenlet/task
```

CPU time is measured at the OS-thread level, then assigned to whichever greenlet/task was
`on_cpu`. The required input is the *OS thread's* CPU clock -- exactly what the fix computes,
because `native_id` is that OS thread's real kernel TID. The on-CPU greenlet still gets the
sample. Nothing is lost.

## "Shouldn't ddtrace patch so the id is real?"

It can't, by design. gevent deliberately monkey-patches `_thread.get_ident` to return
`id(greenlet)` so cooperative greenlets look like threads to `threading`. dd-trace-py cannot
override that without breaking gevent's model. So `Thread.ident` under gevent is inherently a
greenlet id, not a `pthread_t`. The correct data source for a CPU clock is the real
`native_id`, which is what the fix uses.

## The fix

`ddtrace/internal/datadog/profiling/stack/echion/echion/threads.h`, Linux branch of
`ThreadInfo::create`: instead of

```c
clockid_t cpu_clock_id;
if (pthread_getcpuclockid(static_cast<pthread_t>(thread_id), &cpu_clock_id)) {
    return ErrorKind::ThreadInfoError;
}
```

build the per-thread CPU clock id directly from the kernel TID (`native_id`), the same value
glibc would have read from `pd->tid`:

```c
constexpr clockid_t CPUCLOCK_SCHED = 2;
constexpr clockid_t CPUCLOCK_PERTHREAD_MASK = 4;
clockid_t cpu_clock_id =
  (~static_cast<clockid_t>(native_id) << 3) | (CPUCLOCK_SCHED | CPUCLOCK_PERTHREAD_MASK);
```

Why it's safe:
- No untrusted pointer is dereferenced, so there is no OOB read and no `SEGV_ACCERR`,
  regardless of what `thread_id` is.
- For real OS threads, `native_id` is the kernel TID, so the computed clock id is identical
  to what `pthread_getcpuclockid` produced -- behavior unchanged.
- If the TID is stale/invalid, `clock_gettime` returns `EINVAL`, already handled gracefully
  by `update_cpu_time` (skips CPU time instead of crashing).

## Open design question (separate from this crash)

The sampler keys the thread map by the interpreter's `PyThreadState.thread_id`:

```c
auto it = echion.thread_info_map().find(tstate.thread_id);
if (it == echion.thread_info_map().end())
    continue;
```

but `register_thread` under gevent inserts entries keyed by the **greenlet id**
(`Thread.ident`). Whether those keys line up with `tstate.thread_id` under gevent -- i.e.
whether registering each greenlet as a "thread" is even the intended behavior versus
registering the underlying OS threads -- is a real design question, but it is orthogonal to
this crash. The fix neither introduces nor worsens it.

---
*Follow-up to `crashes/crash46.md`. Triage aid; engineers should review before acting.*
