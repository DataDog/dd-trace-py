# CPython 3.14 → 3.15 Change Analysis (for echion)

**Generated from:** `git diff v3.14.0 v3.15.0a7` on `python/cpython`
**Latest 3.15 tag used:** `v3.15.0a7` (pre-release; verify against final tag when available)
**Raw diff:** `cpython_314_to_315_headers.diff` (1,479 lines)

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
- Any code reading `_PyInterpreterFrame.f_frame_state` and comparing against old
  constants will silently misclassify frames (e.g., `FRAME_EXECUTING = 0` in 3.14
  now means `FRAME_CREATED` in 3.15).
- `FRAME_COMPLETED` is gone — code checking `>= FRAME_COMPLETED` will break.
- New `FRAME_SUSPENDED_YIELD_FROM_LOCKED` needs to be included in suspended checks.
- **Use the `FRAME_STATE_SUSPENDED` / `FRAME_STATE_FINISHED` macros** instead of
  hardcoding values, so the `#if PY_VERSION_HEX` guard only needs to cover the
  macro definitions, not every use site.

**Files to update:** `echion/frame.h`, `echion/state.h`, any caller that checks
`frame_state` directly.

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
- Tagged int shift changed: `(i << 2)` instead of `(i << 2)` — same for non-debug,
  but `Py_TAGGED_SHIFT = 2` is now the canonical name.

**Echion impact:**
- The `PyStackRef_AsPyObjectBorrow(f->f_executable)` call to recover a `PyObject*`
  from a frame's executable field **still works** — no change to the public API.
- If echion directly manipulates `.bits` (e.g., checking `(bits & 1)`), update to
  use the new named constants.
- If echion uses `PyStackRef_FromPyObjectImmortal()`, rename to
  `PyStackRef_FromPyObjectBorrow()` under a `#if PY_VERSION_HEX >= 0x030f0000` guard.
- Free-threaded builds: `Py_TAG_DEFERRED` no longer exists; use `Py_TAG_REFCNT`.

---

## Additive / Beneficial Changes (no breakage, consider adopting)

### 4. `_PyFrame_SafeGetCode()` and `_PyFrame_SafeGetLasti()` — `pycore_interpframe.h`

New in 3.15, **explicitly designed for profilers and debuggers**:

```c
// Returns NULL if frame is invalid or freed (heuristic, not 100% reliable)
static inline PyCodeObject* _Py_NO_SANITIZE_THREAD
_PyFrame_SafeGetCode(_PyInterpreterFrame *f);

// Returns -1 if frame is invalid or freed
static inline int _Py_NO_SANITIZE_THREAD
_PyFrame_SafeGetLasti(struct _PyInterpreterFrame *f);
```

**Recommendation:** Under `#if PY_VERSION_HEX >= 0x030f0000`, use
`_PyFrame_SafeGetCode()` instead of `_PyFrame_GetCode()` in echion's frame-reading
path. It checks for freed memory (globals/builtins NULL, `_PyMem_IsPtrFreed`,
`_PyObject_IsFreed`, `PyCode_Check`) before dereferencing.

---

### 5. `base_frame` sentinel in `_PyThreadStateImpl` — `pycore_tstate.h`

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

### 6. `_Py_AsyncioDebug` symbol rename — `_asynciomodule.c`

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

### 7. Other `_PyThreadStateImpl` additions — `pycore_tstate.h`

New fields (low echion impact):

- `c_stack_init_base` / `c_stack_init_top` — stack protection reset values
- `generator_return_kind` enum — distinguishes yield vs return in `gen_send_ex2()`
- `pystats_struct` (under `Py_STATS`)
- `jit_tracer_state` (under `_Py_TIER2`)
- `__padding[64]` (GIL-disabled, cache-line alignment)

These add fields **after** `asyncio_running_loop` / `asyncio_tasks_head`, so if
echion accesses those by name (not by offset), no change needed. If accessing by
raw offset, regenerate offsets.

---

## Work checklist for echion 3.15 port

- [x] Add `#if PY_VERSION_HEX >= 0x030f0000` guard with new `PyFrameState` values
      (renumbered) and new `FRAME_SUSPENDED_YIELD_FROM_LOCKED` state.
      → `tasks.h`: new `PyGen_yf` branch for 3.15; `FRAME_SUSPENDED_YIELD_FROM_LOCKED`
        is only reachable in free-threaded builds so it is guarded with
        `#ifdef Py_GIL_DISABLED`. GIL builds behave identically to 3.14.
- [x] Update `FRAME_STATE_SUSPENDED` / `FRAME_STATE_FINISHED` usage to use macros.
      → Not applicable: echion uses enum constants by name (not hardcoded values),
        so the renumbering has no effect. `FRAME_STATE_SUSPENDED`/`FRAME_STATE_FINISHED`
        macros are not used in echion code.
- [x] Remove any reference to `FRAME_COMPLETED` under 3.15 path.
      → Not applicable: `FRAME_COMPLETED` is not referenced in echion's codebase.
- [x] Remove `FRAME_OWNED_BY_CSTACK` reference under 3.15 guard.
      → `frame.cc`: split `>= 0x030e0000` into `>= 0x030f0000` (no CSTACK) and
        `>= 0x030e0000` (CSTACK + INTERPRETER). Also fixed `is_entry` assignment.
- [x] Rename `PyStackRef_FromPyObjectImmortal` → `PyStackRef_FromPyObjectBorrow`
      (if used) under 3.15 guard.
      → Not applicable: `PyStackRef_FromPyObjectImmortal` is not used in echion's codebase.
- [ ] Consider adopting `_PyFrame_SafeGetCode()` for safer frame reading.
- [ ] Consider using `base_frame` sentinel for frame-chain termination.
- [x] Update asyncio debug symbol lookup: `_AsyncioDebug` → `_Py_AsyncioDebug`.
      → Not applicable: echion does not look up the asyncio debug symbol by name.
- [x] Update CI/build matrix: add `cp315-*` wheels, Python 3.15 test variants.
      → Done: `pyproject.toml`, `riotfile.py`, `.gitlab/package.yml`,
        `.gitlab/testrunner.yml`, `.gitlab/templates/build-base-venvs.yml`,
        `.gitlab/templates/detect-global-locks.yml`, `.gitlab/multi-os-tests.yml`,
        `.gitlab-ci.yml`, `.github/workflows/generate-package-versions.yml`,
        `.github/workflows/generate-supported-versions.yml`.
- [ ] Run echion test suite against a CPython 3.15 build and confirm green.
