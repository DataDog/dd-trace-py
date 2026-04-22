# Native Code Review Guide — Detailed Reference

Review guidance for C, C++, Rust, and Cython code in dd-trace-py. Each rule
is derived from production incidents. This applies to any code that crosses a
language boundary, manages threads or the GIL, or runs in fork handlers or
allocator hooks. For IAST taint tracking code, also consult
`.cursor/rules/iast.mdc`.

**How to use:** When reviewing native code (C/C++/Rust/Cython), work through
each applicable section. Include your assessment in the PR
description (e.g., "GIL lifecycle: no new PyEval_RestoreThread calls" or
"Fork safety: Events recreated in _after_fork_child"). For the compact
triage checklist with trigger symbols and stop conditions, see
`.cursor/rules/native-code.mdc`.

**Maintenance:** Last updated 2026-03-09. If this document is more than 3
months old, ask a human reviewer to refresh it with recent `fix(core)`,
`fix(profiling)`, and `fix(internal)` PRs.

---

## 1. GIL Lifecycle During Finalization

### What goes wrong

Code checks `py_is_finalizing()` then calls `PyGILState_Ensure()` or
`PyEval_RestoreThread()`. Finalization starts between the check and the call
(TOCTTOU race). On CPython 3.12, `take_gil()` calls `PyThread_exit_thread()`
which triggers `pthread_exit()`. On glibc, this throws `abi::__forced_unwind`
through C++ destructors, hitting `std::terminate` (SIGABRT). On CPython
3.13.8+, the thread hangs forever in `pause()` instead.

**musl libc (Alpine Linux):** `pthread_exit()` uses `longjmp` instead of C++
exceptions. `catch(abi::__forced_unwind&)` will not fire, and C++ destructors
will not be called during forced unwind. The `py_is_finalizing()` check is the
only defense on musl.

### Review checklist

- Does this code call `PyGILState_Ensure()`, `PyEval_RestoreThread()`,
  `PyEval_SaveThread()`, or the `Py_BEGIN_ALLOW_THREADS` /
  `Py_END_ALLOW_THREADS` macros (which expand to `PyEval_SaveThread()` /
  `PyEval_RestoreThread()`)? Is there a `py_is_finalizing()` check before each?
- **TOCTTOU is inherent** — the check-then-call can never be fully atomic.
  Is there a `try/catch(abi::__forced_unwind&)` wrapping the code that might
  trigger `pthread_exit()`? The catch must re-throw (glibc aborts if swallowed).
- Does the RAII wrapper track whether the operation completed? (Use
  `_acquired`/`_saved` flags — don't leave `_state` uninitialized when the
  constructor skips.)
- If the GIL cannot be acquired (finalizing), does the caller bail out early
  without calling any Python C API?
- **For Rust/pyo3 code:** `abi::__forced_unwind` cannot propagate through Rust
  `extern "C"` boundaries. The Rust code itself needs a finalization check
  before re-acquiring the GIL (e.g., before `py.detach()` closure returns).
- Does this thread do blocking I/O in its callback? Blocking I/O without
  timeouts makes the thread unkillable during shutdown, guaranteeing it's
  alive at finalization.

### CPython version behavior

| Version | Behavior when `take_gil()` detects finalization |
|---------|------------------------------------------------|
| 3.12 (all) | `PyThread_exit_thread()` -> `pthread_exit()` -> crash |
| 3.13.0-3.13.7 | Same as 3.12 |
| 3.13.8+ | `PyThread_hang_thread()` -> `pause()` forever (hang, not crash) |
| 3.14+ | Hangs until program exits ([official](https://docs.python.org/3/c-api/threads.html)); `PyThread_exit_thread()` deprecated |
| 3.15+ | `PyGILState_Ensure()` itself hangs safely |

`py_is_finalizing()` checks are essential on all versions. On 3.13.8+,
they prevent infinite hangs instead of crashes. Note that `PyGILState_Ensure`,
`PyEval_RestoreThread`, `PyEval_AcquireThread`, and `PyThreadState_Swap` all
hang during finalization in 3.14+.

**Other 3.14+ behavior changes** (not `take_gil()`-specific):
- `_Py_Dealloc` dereferences `tstate` immediately — calling `Py_DECREF`
  during finalization (when tstate may be NULL) crashes. See §3.
- Default multiprocessing start method changed from `fork` to `forkserver`,
  requiring lock wrappers to be picklable. See §8.

### Historical examples

- TOCTTOU race in `AllowThreads::~AllowThreads()` caused SIGSEGV at
  `__pthread_mutex_lock` ([#16721](https://github.com/DataDog/dd-trace-py/pull/16721))
- `_Unwind_ForcedUnwind` -> `std::terminate` when `pthread_exit()` unwound
  through `std::thread` callable ([#16729](https://github.com/DataDog/dd-trace-py/pull/16729))
- `TraceExporterPy::send` -> `PyEval_RestoreThread` in Rust caused SIGABRT
  because `__forced_unwind` can't cross Rust FFI

---

## 2. Fork Safety for Synchronization Primitives

### What goes wrong

After `fork()`, any lock held by a thread in the parent is in an **undefined
state** in the child (POSIX). This applies to all synchronization primitives:
`pthread_mutex_t`, `pthread_rwlock_t`, `pthread_cond_t`, `std::mutex`,
`std::condition_variable`, and any wrapper around them. Calling `.lock()`,
`.clear()`, or `.wait()` on these corrupted primitives causes SIGSEGV.

`_after_fork` is called in both the **parent** (to restart threads) and the
**child** (to start fresh). These have different requirements:
- **Child:** No other threads exist. Recreate primitives from scratch
  (e.g., `std::make_unique<Event>()` or placement `new` on the mutex).
- **Parent:** Other threads may hold references. Use `.clear()` to preserve
  references.

### Review checklist

- Does this code introduce or modify any lock, mutex, condition variable, or
  synchronization primitive? Is it reset in `_after_fork` / `_after_fork_child`
  / `pthread_atfork` child handler?
- In the child path: are primitives **recreated**, not just cleared?
- In the parent path: are primitives **cleared**, not recreated? Other threads
  may hold references to the existing objects.
- Does `_before_fork` stop and join all threads before fork? What if a thread
  doesn't respond to the stop signal (blocked in I/O)?
- Can `fork()` happen from another thread while a thread start is blocked in
  `_started->wait()`? If so, the child inherits a locked mutex.
- Does this change add or reorder `pthread_atfork` handlers? What is the full
  handler execution order? Can any handler access state freed by an earlier
  handler?
- Prefer a small number of centralized `atfork` handlers over many independent
  ones — independent handlers are hard to reason about for ordering.
- Does this behavior respect uWSGI `--skip-atexit`?

### Historical examples

- `_after_fork` called `.clear()` on Event objects with corrupted mutexes
  ([#16718](https://github.com/DataDog/dd-trace-py/pull/16718))
- Event recreation for both paths broke the parent-side `awake()` handshake.
  Fixed by parent/child split in [#16721](https://github.com/DataDog/dd-trace-py/pull/16721).
- C++ mutexes locked by parent threads that don't exist in child caused
  deadlocks ([#11768](https://github.com/DataDog/dd-trace-py/pull/11768))
- Fork handler freed `ProfilesDictionary`, then later handler tried to use it
  ([#16257](https://github.com/DataDog/dd-trace-py/pull/16257))
- uWSGI `--skip-atexit` caused unsafe atexit handler registration
  ([#16353](https://github.com/DataDog/dd-trace-py/pull/16353))

---

## 3. Object Lifetime Across Thread Boundaries

### What goes wrong

A Python object is passed to a native thread via raw pointer capture in a
lambda. If `Py_INCREF` happens inside the thread (after `std::thread`
creation), there's a window where the object can be deallocated before the
thread increments the refcount. The thread then accesses freed memory.

### Review checklist

- When passing a Python object to a native thread, is `Py_INCREF` called
  **before** `std::thread` creation, not inside the thread lambda?
- Does the thread take ownership of the stolen reference without
  double-incrementing?
- If the thread bails out early (can't acquire GIL), is the reference properly
  handled? (Leak is acceptable during finalization; `Py_DECREF` without GIL
  is not.)
- After signaling "done" (e.g., setting a flag, notifying a condition variable),
  does the thread still execute any Python operations like `Py_DECREF`? If so,
  `join()` callers may return while the thread is still accessing Python objects,
  causing use-after-free on Python 3.14+ where `_Py_Dealloc` dereferences
  `tstate` immediately.

### Historical examples

- `Py_INCREF` inside thread lambda left a use-after-free window
  ([#16721](https://github.com/DataDog/dd-trace-py/pull/16721))
- `Py_DECREF` in thread destructor ran after thread signaled completion,
  `join()` returned while background thread still ran refcounting
  ([#16055](https://github.com/DataDog/dd-trace-py/pull/16055))

---

## 4. Memory Safety in Native Code

### What goes wrong

**Reentrance in allocator hooks:** Code running inside `malloc`/`free` hooks
calls Python C API functions that trigger allocations. This creates reentrant
`malloc`/`free` calls that corrupt the heap tracker's internal state.

Dangerous functions inside allocator hooks:
- `PyThreadState_GetFrame()` — increfs the frame (allocation)
- `PyFrame_GetBack()` — new reference (allocation)
- `PyFrame_GetCode()` — new reference (allocation)
- `Py_DECREF()` — can trigger deallocation chains
- `PyUnicode_AsUTF8AndSize()` — can allocate UTF-8 cache
- `PyObject_CallObject()` — can release GIL, allowing other threads to observe
  partially-constructed state
- `threading.current_thread()` — Python-level call that releases the GIL,
  crashes on partially-constructed state

Safe alternatives inside allocator hooks:
- `PyThread_get_thread_ident()` / `PyThread_get_thread_native_id()` — no
  allocation, no GIL release
- Direct struct field reads (e.g., `frame->f_code` instead of
  `PyFrame_GetCode()`) — avoids new references and allocation
- `Py_INCREF` — safe (no deallocation chain)

**Integer overflow:** Integer types too small for array sizes cause overflow,
resulting in smaller allocations than needed, leading to out-of-bounds writes.

**Pool/cache hygiene:** Objects returned to pools without clearing state leak
stale data to the next user.

### Review checklist

- Does this code run inside a `malloc`/`free`/`realloc` hook? If yes, does it
  call ANY function that can trigger an allocation or GC? Trace through every
  call — `Py_INCREF` is safe but `Py_DECREF` is not.
- Can this be rewritten to use direct struct field reads instead of Python C
  API calls?
- Is GC explicitly disabled for the duration of the hook? **Verify** the disable
  is actually compiled in (`objdump`/`nm` check — a previous fix was dead code
  from a missing `#include`, [#15388](https://github.com/DataDog/dd-trace-py/pull/15388)).
- What integer type is used for sizes/counts? Can it overflow for realistic
  workloads? Prefer `size_t`.
- When an object is returned to a pool/cache, is all its state cleared?

### Historical examples

- memalloc hook called `Py_INCREF`/`Py_DECREF`/`PyFrame_GetBack()` during
  allocation tracking, causing heap corruption
- `PyObject_CallObject(threading_current_thread)` in memalloc released GIL,
  other threads crashed on partially-constructed objects
  ([#16396](https://github.com/DataDog/dd-trace-py/pull/16396))
- `realloc` hook collected traceback which triggered GC on just-reallocated
  memory ([#14550](https://github.com/DataDog/dd-trace-py/pull/14550))
- `uint16_t` for traceback array size overflowed at 65536, wrote out of bounds
  ([#12286](https://github.com/DataDog/dd-trace-py/pull/12286))
- Pool object returned without `clear_buffers()`, next user got stale data
  ([#16186](https://github.com/DataDog/dd-trace-py/pull/16186))

---

## 5. FFI Exception and Unwind Propagation

### What goes wrong

C++ exceptions (`abi::__forced_unwind` from `pthread_exit`, `std::bad_alloc`)
cannot propagate through certain FFI boundaries:
- **Rust `extern "C"`**: Rust aborts on foreign exceptions.
- **Python C API**: CPython is C, not C++. Exceptions crossing from C++ through
  CPython back to C++ may not propagate correctly.
- **`noexcept` functions**: C++ destructors are implicitly `noexcept`. If
  `__forced_unwind` propagates through a destructor, `std::terminate` is called.
- **musl libc**: Uses `longjmp` for `pthread_exit`, not C++ exceptions. The
  `catch(abi::__forced_unwind&)` handler never fires on Alpine Linux.

### Review checklist

- Does this code cross a C/C++/Rust/Python boundary? Can an exception or
  signal occur inside the foreign code?
- For Rust/pyo3: does the Rust code call `PyEval_RestoreThread` or equivalent?
  If so, it needs its own finalization check — the C++ caller's `try/catch`
  won't help.
- For C++ thread bodies (`std::thread` callables): is there a
  `try/catch(abi::__forced_unwind&)` that re-throws?
- Does the `catch(...)` block signal all necessary events to prevent deadlocks?
  Does it risk masking non-shutdown exceptions (e.g., `std::bad_alloc`)?
- Does any function return a pointer or reference to data protected by a mutex?
  Return copies instead.
- Does this native code call `getenv()` or `std::env::var()`? These are not
  thread-safe when the environment is modified concurrently.

### Historical examples

- `TraceExporterPy::send` in Rust: `pthread_exit` from `PyEval_RestoreThread`
  aborted at the Rust FFI boundary
- `PeriodicThread_start` lambda: `__forced_unwind` escaped `std::thread`
  callable, hit `std::terminate`
  ([#16729](https://github.com/DataDog/dd-trace-py/pull/16729))
- `get_active_span_from_thread_id` returned a pointer after releasing the
  mutex, concurrent modification caused data race
  ([#11167](https://github.com/DataDog/dd-trace-py/pull/11167))

---

## 6. Unbounded Loops and Recursion

### What goes wrong

Code iterates over CPython internal linked lists (interpreters, greenlets,
stack chunks) without an upper bound. Corrupted or cyclic data structures
cause infinite loops, often while holding a lock. Calling Python-level
functions (logging, exceptions) from instrumentation code can re-enter the
instrumented primitives, causing infinite recursion.

### Review checklist

- Does this code loop over any external or system data structure (CPython
  linked list, greenlet chain, stack chunks)? What is the iteration upper
  bound? If no natural bound exists, add a hard limit (e.g., 10,000 for
  interpreter/thread lists) and log/break when exceeded.
- Can any function called from this instrumentation code acquire a lock that
  we instrument? Trace the full call chain.
- Is there any recursive function? What limits the recursion depth?
- If this code holds a lock, can any function it calls block indefinitely?

### Historical examples

- Loop over interpreters via linked list with no bound caused infinite loop
  ([#16002](https://github.com/DataDog/dd-trace-py/pull/16002))
- `unwind_greenlets()` looped unboundedly while holding lock, causing deadlock
  ([#15973](https://github.com/DataDog/dd-trace-py/pull/15973))
- Lock profiler `_release` called `LOG.debug()`, logging acquired a lock,
  infinite recursion ([#13147](https://github.com/DataDog/dd-trace-py/pull/13147))

---

## 7. Build and Release Validation

### What goes wrong

Bugs that only manifest in release builds (wheels) but not in development
builds. Preprocessor macros that compile to nothing because of missing
includes. CMake relative paths that work during development but break in the
wheel directory layout.

### Review checklist

- Does this fix depend on preprocessor macros or conditional compilation? Are
  the headers that define them included? Verify the fix is present in compiled
  output, not just in source code.
- **Flag for human verification:** If this change affects build layout, linking,
  or conditional compilation, it must be tested on an actual release wheel
  (in-tree/riot builds may not reproduce wheel-specific issues).
- For linking changes, have the symbols been verified present in the final
  `.so`?

### Historical examples

- CMake path had an extra `../`, causing `undefined symbol` in release wheels
  only ([#15818](https://github.com/DataDog/dd-trace-py/pull/15818))
- GC disable fix used macros but didn't `#include` the defining header, so the
  fix was silently dead code
  ([#15388](https://github.com/DataDog/dd-trace-py/pull/15388))
- Reentrancy guard intended to be `thread_local` was declared incorrectly,
  resulting in a global variable
  ([#12526](https://github.com/DataDog/dd-trace-py/pull/12526))

---

## 8. Dependency Version Awareness

### What goes wrong

New Python versions change defaults, add safety checks, deprecate internal
APIs, or break assumptions about finalization order and GIL behavior.
libdatadog and libddwaf APIs and behavior also change between versions.
Crashes have been misattributed due to wrong assumptions about library
internals (e.g., `ddwaf_context_init` crash misattributed as `ddwaf_run` in
stripped binaries).

### Review checklist

- Does this code use CPython internal APIs or access internal struct fields?
  Check the supported version range in `pyproject.toml` (`requires-python`)
  and verify behavior across all supported versions. Use the
  `compare-cpython-versions` skill when adding support for a new version.
- **Read the actual source code** rather than guessing. For CPython, use
  [github.com/python/cpython](https://github.com/python/cpython) and checkout
  the relevant version tag. For libdatadog, check the pinned version in
  `src/native/Cargo.toml`. For libddwaf, check the pinned version in
  `setup.py`. Verify behavior against that version's source, not the latest.
- Does this code depend on Python's default behavior for multiprocessing start
  method, GC scheduling, or finalization order?
- Has this been tested against the latest Python RC/beta?

### Historical examples

- Python 3.14 changed default multiprocessing start method from fork to
  forkserver, requiring lock wrappers to be picklable
  ([#15899](https://github.com/DataDog/dd-trace-py/pull/15899))
- Python 3.14 `_Py_Dealloc` dereferences `tstate` immediately, crashing if
  finalization set it to NULL
  ([#16055](https://github.com/DataDog/dd-trace-py/pull/16055))
- CPython 3.13 introduced official `PyGen_yf` replacing internal access
  ([#15450](https://github.com/DataDog/dd-trace-py/pull/15450))

---

## 9. Validating Fixes in Native Code

### What goes wrong

Fixes that appear correct in code review but don't actually work: dead code
from missing includes, thread-safety fixes that don't survive real concurrency,
features that crash when combined with other features under fork.

### Review checklist

- If this interacts with fork, has it been tested with **all** features enabled
  simultaneously (tracing + profiling + AppSec)?
- If this fix uses preprocessor macros or conditional compilation, has the
  compiled output been verified to contain the fix?
- Does the test actually exercise the fixed code path? (A test that passes
  both with and without the fix is not a valid test.)
- What is the rollback plan if this fix causes regressions? Can it be
  feature-flagged?

### Historical examples

- String interning into libdatadog had to be **reverted** because it crashed
  with memory profiling + fork
  ([#16243](https://github.com/DataDog/dd-trace-py/pull/16243))
- Rust rate limiter mutex fix **reverted** because it didn't solve the
  thread-safety problem under real concurrency; went back to pure Python
  ([#10176](https://github.com/DataDog/dd-trace-py/pull/10176),
  [#10225](https://github.com/DataDog/dd-trace-py/pull/10225))
- GC disable fix was **dead code** from a missing `#include`
  ([#15388](https://github.com/DataDog/dd-trace-py/pull/15388))

---

## 10. Cython Code (.pyx / .pxd)

### What goes wrong

**Silent exception swallowing:** A `cdef` function that can raise a Python
exception but lacks an `except` clause silently discards the exception. The
caller sees a zero/NULL return with no error set, and continues with corrupt
state.

**String lifetime with `string_view`:** `PyUnicode_AsUTF8AndSize` returns a
pointer into the Python object's internal buffer. If that object is garbage
collected while a `string_view` or raw `const char*` still points to it, the
pointer dangles. This is especially subtle when building a C++ container of
`string_view`s in a loop — the source strings must be kept alive for the
entire duration.

**Unsafe casts without type validation:** Casting an arbitrary `PyObject*` to a
CPython internal struct pointer (e.g., `PyFrameObject*`) without an
`isinstance()` check crashes if the object is the wrong type.

### Review checklist

- Does every `cdef` function that can raise an exception declare `except *`,
  `except -1`, or `except? -1`? Without this, exceptions are silently
  swallowed. Prefer `except? -1` for int-returning functions (checks return
  value, then checks for exception) over `except -1` (assumes -1 always means
  error).
- Do `nogil` blocks avoid all Python C API calls and `PyObject*` access? Only
  pure C/C++ operations are safe inside `nogil`.
- When `PyUnicode_AsUTF8AndSize` is used to create `string_view` or raw
  `const char*` values, are the source Python strings kept alive (e.g., stored
  in a list) for the full lifetime of the native pointers?
- Do `cdef class` types with native resources (pointers, buffers) implement
  `__dealloc__` with NULL checks before cleanup and defensive NULL assignment
  after?
- Are raw casts to CPython internal struct types (e.g., `<PyFrameObject*>`)
  preceded by an `isinstance()` check?
- Does `PyMem_Malloc` / `PyMem_Realloc` check for NULL return before use?
- If buffer operations can fail mid-way, is there a rollback path (restore
  previous length/size)?

### Patterns from the codebase

**Exception declaration (correct):**
```cython
# _encoding.pyx — except? -1 checks return value then exception state
cdef inline int pack_number(msgpack_packer *pk, object n) except? -1:
```

**String lifetime (correct):**
```cython
# _ddup.pyx — keeps source strings alive while string_views exist
endpoint_list = []  # prevents GC of source strings
for endpoint in endpoints:
    endpoint_list.append(endpoint)
    utf8_data = PyUnicode_AsUTF8AndSize(endpoint, &utf8_size)
    container.insert(string_view(utf8_data, utf8_size))
```

**Safe cast (correct):**
```cython
# _ddup.pyx — isinstance before unsafe cast
if not isinstance(frame, types.FrameType):
    return
frame_ptr = <PyFrameObject*><PyObject*>frame
```

**Dealloc (correct):**
```cython
# _ddup.pyx — NULL check + defensive NULL assignment
def __dealloc__(self):
    if self.ptr is not NULL:
        ddup_drop_sample(self.ptr)
        self.ptr = NULL
```

