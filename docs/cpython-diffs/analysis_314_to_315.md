# CPython 3.14 → 3.15 Change Analysis (for echion)

**Generated from:** `git diff v3.14.0 v3.15.0rc3` on `python/cpython` over the compared paths below (first drafted against `v3.15.0a7`; re-checked through `v3.15.0rc3`).
**Latest 3.15 tag used:** `v3.15.0rc3` (pre-release; verify against final tag when available)
**Compared paths** (reproduce the header/ABI slice with these exact args):

```bash
git diff v3.14.0 v3.15.0rc3 -- \
  Include/cpython/genobject.h \
  Include/internal/pycore_frame.h \
  Include/internal/pycore_interpframe.h \
  Include/internal/pycore_interpframe_structs.h \
  Include/internal/pycore_llist.h \
  Include/internal/pycore_runtime.h \
  Include/internal/pycore_stackref.h \
  Include/internal/pycore_tstate.h \
  Modules/_asynciomodule.c
```

The a8 await-stack layout change (§4) also depends on `Python/bytecodes.c` /
`Objects/genobject.c`, which are outside that header-only set.
**Raw diff (local research artifact, not committed):** regenerate with the `git diff`
above into e.g. `/tmp/cpython_314_to_315_headers.diff`; the 3.13→3.14 committed
reference lives in `DataDog/echion` at
`docs/cpython-diffs/cpython_313_to_314_headers.diff`.

Files with **no changes** relevant to echion (stable between 3.14 and 3.15):

- `Include/cpython/genobject.h` — generator object layout unchanged
- `Include/internal/pycore_llist.h` — llist API unchanged
- `Include/internal/pycore_runtime.h` — runtime struct unchanged

---

## Breaking Changes (must fix to compile/run correctly)

### 1. `PyFrameState` enum completely renumbered — `pycore_frame.h`

**Priority: HIGH**

| State | 3.14 value | 3.15 value |
|---|---|---|
| `FRAME_CREATED` | -3 | 0 |
| `FRAME_SUSPENDED` | -2 | 1 |
| `FRAME_SUSPENDED_YIELD_FROM` | -1 | 2 |
| `FRAME_SUSPENDED_YIELD_FROM_LOCKED` | *(new)* | 3 |
| `FRAME_EXECUTING` | 0 | 4 |
| `FRAME_COMPLETED` | 1 | *(removed)* |
| `FRAME_CLEARED` | 4 | 5 |

Changed macros:

```c
// 3.14
#define FRAME_STATE_SUSPENDED(S) ((S) == FRAME_SUSPENDED || (S) == FRAME_SUSPENDED_YIELD_FROM)
#define FRAME_STATE_FINISHED(S)  ((S) >= FRAME_COMPLETED)

// 3.15
#define FRAME_STATE_SUSPENDED(S) ((S) >= FRAME_SUSPENDED && (S) <= FRAME_SUSPENDED_YIELD_FROM_LOCKED)
#define FRAME_STATE_FINISHED(S)  ((S) == FRAME_CLEARED)
```

**Echion impact:**
- Echion reads `PyGenObject.gi_frame_state` (not `_PyInterpreterFrame.f_frame_state`,
  which does not exist) in `stack/src/echion/tasks.cc` and
  `stack/echion/echion/cpython/tasks.h`. Comparing against old constants silently
  misclassifies frames (e.g., `FRAME_EXECUTING = 0` in 3.14 now means
  `FRAME_CREATED` in 3.15).
- `FRAME_COMPLETED` is gone — code checking `>= FRAME_COMPLETED` will break.
- New `FRAME_SUSPENDED_YIELD_FROM_LOCKED` needs to be included in suspended checks.
- **Use the `FRAME_STATE_SUSPENDED` / `FRAME_STATE_FINISHED` macros** instead of
  hardcoding values, so the `#if PY_VERSION_HEX` guard only needs to cover the
  macro definitions, not every use site.

**Files to update:** `stack/src/echion/tasks.cc`,
`stack/echion/echion/cpython/tasks.h` (and any other `gi_frame_state` readers).
`frame.h` / `state.h` do not check frame state.

**Guard:** `#if PY_VERSION_HEX >= 0x030f0000`

---

### 2. `FRAME_OWNED_BY_CSTACK` removed — `pycore_interpframe_structs.h`

**Priority: LOW**

```c
// 3.14
enum _frameowner {
    FRAME_OWNED_BY_THREAD = 0,
    FRAME_OWNED_BY_GENERATOR = 1,
    FRAME_OWNED_BY_FRAME_OBJECT = 2,
    FRAME_OWNED_BY_INTERPRETER = 3,
    FRAME_OWNED_BY_CSTACK = 4,   // <-- removed in 3.15
};
```

**Echion impact:** If any code checks `frame->owner == FRAME_OWNED_BY_CSTACK`,
wrap in `#if PY_VERSION_HEX < 0x030f0000`.

---

### 3. `_PyStackRef` tag scheme unified — `pycore_stackref.h`

**Priority: MEDIUM** (mostly affects free-threaded builds)

Key changes:

- Tag constants moved to top-level (no longer split between GIL/nogil paths):
  ```c
  #define Py_INT_TAG    3
  #define Py_TAG_INVALID 2   // new: marks ERROR sentinel
  #define Py_TAG_REFCNT 1
  #define Py_TAG_BITS   3
  #define Py_TAGGED_SHIFT 2  // new
  ```
- `Py_TAG_DEFERRED` (free-threaded) is **gone** — merged with `Py_TAG_REFCNT`.
- `PyStackRef_FromPyObjectImmortal()` **renamed** to `PyStackRef_FromPyObjectBorrow()`.
- New `PyStackRef_ERROR` sentinel (`bits == Py_TAG_INVALID`).
- New predicates: `PyStackRef_IsError()`, `PyStackRef_IsMalformed()`,
  `PyStackRef_IsValid()`.
- New `PyStackRef_Wrap()` / `PyStackRef_Unwrap()` for raw pointer wrapping.
- `INITIAL_STACKREF_INDEX` changed from `8` to `(5 << Py_TAGGED_SHIFT)` = `20`.
- Tagged-int shift **value is unchanged** (`<< 2`); only the name is new —
  `Py_TAGGED_SHIFT` (= `2`) is now the canonical spelling for that shift.

**Echion impact:**
- The `PyStackRef_AsPyObjectBorrow(f->f_executable)` call to recover a `PyObject*`
  from a frame's executable field **still works** — no change to that internal
  helper in `Include/internal/pycore_stackref.h` (not a public compatibility API).
- If echion directly manipulates `.bits` (e.g., checking `(bits & 1)`), update to
  use the new named constants.
- If echion uses `PyStackRef_FromPyObjectImmortal()`, rename to
  `PyStackRef_FromPyObjectBorrow()` under a `#if PY_VERSION_HEX >= 0x030f0000` guard.
- Free-threaded builds: `Py_TAG_DEFERRED` no longer exists; use `Py_TAG_REFCNT`.

---

### 4. Awaited-object stack slot moved (3.15.0a8+) — `pycore_interpframe.h` / genobject

**Priority: HIGH** (asyncio / `PyGen_yf` await-chain walks)

Landed in **3.15.0a8**, after the original a7 header pass. `_SEND_GEN_FRAME` gained
a `null` operand (`Python/bytecodes.c`), so a frame suspended in `YIELD_FROM` holds
`PyStackRef_NULL` at `stackpointer[-1]` and the awaited object at `stackpointer[-2]`.
`_PyFrame_StackPeek` grew a `depth` argument; CPython reads the awaited object as
`_PyFrame_StackPeek(&gen->gi_iframe, 2)` in `gen_getyieldfrom` (`Objects/genobject.c`).

```c
// 3.15.0a7 and earlier
static inline _PyStackRef _PyFrame_StackPeek(_PyInterpreterFrame *f);

// 3.15.0a8+
static inline _PyStackRef _PyFrame_StackPeek(_PyInterpreterFrame *f, int depth);
```

**Echion impact:**
- Remote `PyGen_yf` must read `stackpointer[-2]` (and require `stacktop >= 2`), not
  `[-1]`. Reading `[-1]` masks to `nullptr` and truncates the await chain.
- Implemented in `echion/cpython/tasks.h` (`PY_VERSION_HEX >= 0x030f0000`); covered by
  `stack/test/test_frame_state_315.cpp`.

**Guard:** `#if PY_VERSION_HEX >= 0x030f0000`

---

## Additive / Beneficial Changes (no breakage, consider adopting)

### 5. `_PyFrame_SafeGetCode()` and `_PyFrame_SafeGetLasti()` — `pycore_interpframe.h`

Not new in 3.15. Both helpers exist on CPython 3.14 (`Include/internal/pycore_interpframe.h`;
[gh-140815](https://github.com/python/cpython/issues/140815) / [GH-140921](https://github.com/python/cpython/pull/140921)
3.14 backport). CPython documents them as heuristic helpers for `dump_frame()` in
`Python/traceback.c` (faulthandler): return NULL / `-1` if the frame looks invalid or
freed; **not 100% reliable**.

```c
// Similar to _PyFrame_GetCode(), but return NULL if the frame is invalid or
// freed. Used by dump_frame() in Python/traceback.c. The function uses
// heuristics to detect freed memory, it's not 100% reliable.
static inline PyCodeObject* _Py_NO_SANITIZE_THREAD
_PyFrame_SafeGetCode(_PyInterpreterFrame *f);

// Similar to PyUnstable_InterpreterFrame_GetLasti(), but return -1 if the
// frame is invalid or freed.
static inline int _Py_NO_SANITIZE_THREAD
_PyFrame_SafeGetLasti(struct _PyInterpreterFrame *f);
```

**Recommendation:** Available since 3.14 (`#if PY_VERSION_HEX >= 0x030e0000`).
Optional for echion's frame-reading path instead of `_PyFrame_GetCode()`. It checks
for freed memory (globals/builtins NULL, `_PyMem_IsPtrFreed`, `_PyObject_IsFreed`,
`PyCode_Check`) before dereferencing. Not a 3.15-only profiler API.

---

### 6. `base_frame` sentinel in `_PyThreadStateImpl` — `pycore_tstate.h`

New field, **specifically called out as for profiling/sampling**:

```c
typedef struct _PyThreadStateImpl {
    PyThreadState base;

    // Embedded base frame - sentinel at the bottom of the frame stack.
    // Used by profiling/sampling to detect incomplete stack traces.
    _PyInterpreterFrame base_frame;   // <-- NEW in 3.15

    // ...
    Py_ssize_t refcount;
```

**Recommendation:** Use `&tstate_impl->base_frame` as the termination sentinel when
walking the frame chain under 3.15. Previously echion checked for NULL
`previous_instr` or similar; this explicit sentinel is cleaner.

Guard: `#if PY_VERSION_HEX >= 0x030f0000`

---

### 7. `_Py_AsyncioDebug` symbol rename — `_asynciomodule.c`

```c
// 3.14
GENERATE_DEBUG_SECTION(AsyncioDebug, Py_AsyncioModuleDebugOffsets _AsyncioDebug)

// 3.15
GENERATE_DEBUG_SECTION(AsyncioDebug, Py_AsyncioModuleDebugOffsets _Py_AsyncioDebug)
```

**Echion impact:** Only relevant if echion reads this debug symbol by name from the
process (e.g., via `/proc/pid/maps` or DWARF). Update the symbol name lookup to
`_Py_AsyncioDebug` under a `#if PY_VERSION_HEX >= 0x030f0000` guard.

The `TaskObj` struct layout (fields: `task_name`, `task_awaited_by`, `task_coro`,
`task_node`, `task_is_task`, `task_awaited_by_is_set`) is **unchanged** from 3.14 —
the `cpython/tasks.h` mirror in echion does not need layout changes.

---

### 8. Other `_PyThreadStateImpl` additions — `pycore_tstate.h`

New fields (low echion impact):

- `c_stack_init_base` / `c_stack_init_top` — stack protection reset values
- `generator_return_kind` enum — distinguishes yield vs return in `gen_send_ex2()`
- `pystats_struct` (under `Py_STATS`)
- `jit_tracer_state` (under `_Py_TIER2`)
- `__padding[64]` (GIL-disabled, cache-line alignment)

These add fields **after** `asyncio_running_loop` / `asyncio_tasks_head`, so if
echion accesses those by name (not by offset), no change needed. If accessing by
raw offset, regenerate offsets.

ABI fixes that landed from this analysis are in #19269 / #19272. Packaging /
official-support follow-ups are not this file.
