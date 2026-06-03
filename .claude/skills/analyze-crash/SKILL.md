---
name: analyze-crash
description: Native crash log analysis for dd-trace-py
argument-hint: <crash-uuid-or-paste-log>
disable-model-invocation: false
context: fork
agent: general-purpose
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
  - WebSearch
---

# Native Crash Stack Trace Analysis for dd-trace-py

You are analyzing a native crash captured by dd-trace-py's crashtracker. Perform a comprehensive
investigation to help engineers understand and triage the crash. The crashtracker captures **all
process-level native crashes**, so the crashing code may or may not be in dd-trace-py itself —
investigate the code and patterns thoroughly before making the attribution call.

## Input Processing

The user will provide crash data in one of two ways:

### Option A: Crash UUID (preferred)

The user provides a crash UUID from Datadog crash telemetry. Use the fetch script bundled with
this skill to retrieve and process the full crash log:

```bash
cd .claude/skills/analyze-crash && dd-auth .venv/bin/python fetch_crash.py <UUID> [--tracer-version X.Y.Z] [--days 14]
```

This fetches the crash log from Datadog, processes the stack frames (demangling C++ symbols,
resolving addresses against `/proc/self/maps`), and outputs structured JSON with:
- **Metadata**: tracer version, runtime version, service, org, platform, architecture, signal
- **Processed stack**: each frame with demangled symbol, binary file, offset, source file/line
- **Binary mappings**: summary of loaded shared objects
- **Datadog log link**: direct URL to the crash event

If the user provides only a UUID with no other context, ask:

> What is the crash UUID? Any additional filters (tracer version, org ID, language)?

**Setup** (one-time): run `cd .claude/skills/analyze-crash && uv sync` to create the venv and
install dependencies.

**Authentication**: use `dd-auth` as a command wrapper — it injects Datadog API credentials
into the environment for the wrapped process.

### Option B: Pasted crash log or stack trace

The user pastes a raw crash log or stack trace directly. The format is typically:

```
0x7f6e81c51fb7 PyUnicode_AsUTF8AndSize
0x7f6e769d7850 pycbc::Connection::open_bucket(_object*)
0x7f6e81ccd60c
0x7f6e81c53239 _PyObject_Call
...
```

Frames may have: hex address only, hex address + symbol, or symbol + `.so` name + offset. Parse
all available information. If the paste includes full crash JSON (with `error.stack` and
`files./proc/self/maps`), extract the metadata and processed stack from it.

### Determine the source revision

The crash may come from a released wheel or a branch other than `main`. GitHub links must point
at the exact revision that built the crashing binary so that line numbers match.

**Ask the user**: Before starting analysis, ask:

> Do you know the commit SHA or release tag for the dd-trace-py version that crashed?
> (e.g., `v2.18.0`, `abc1234`). If not, I'll use `main`.

**Resolution order** (use the first that succeeds):

1. **User-provided value** — commit SHA or tag supplied in the prompt or in response to the
   question above. Use it verbatim as `GIT_REF`.
2. **Crash metadata** — if the crash was fetched via UUID and includes a `tracer_version`
   field (e.g., `2.18.0`), try the corresponding git tag (`v2.18.0`). Verify it exists:
   `git rev-parse --verify "v2.18.0" 2>/dev/null`.
3. **Version string in the stack trace** — if the wheel filename or ddtrace version appears in
   the trace (e.g., `ddtrace-2.18.0`), try the corresponding git tag.
4. **Current checkout** — if the local repo is not on `main`, use `git rev-parse HEAD` to get
   the current SHA.
5. **Fallback** — use `main`.

Store the resolved value as `GIT_REF` and use it in all GitHub links for the rest of the
analysis. State which ref you are using and why at the top of the report (in Additional Context).

## Analysis Workflow

### GitHub Link Generation

When referencing files in dd-trace-py, always provide clickable GitHub links using the resolved
`GIT_REF` (see "Determine the source revision" above).

**Format**:
```
[filename:line](https://github.com/DataDog/dd-trace-py/blob/{GIT_REF}/path/to/file#Lline)
```

**Examples** (assuming `GIT_REF=v2.18.0`):
- Single line: `[_threads.cpp:226](https://github.com/DataDog/dd-trace-py/blob/v2.18.0/ddtrace/internal/_threads.cpp#L226)`
- Range: `[_threads.cpp:226-252](https://github.com/DataDog/dd-trace-py/blob/v2.18.0/ddtrace/internal/_threads.cpp#L226-L252)`

**Path construction**: base URL is `https://github.com/DataDog/dd-trace-py/blob/{GIT_REF}/`,
then append the repo-relative path (strip any build-specific prefixes like `/home/runner/work/`,
`/usr/local/lib/`, etc.).

---

### Phase 1: Parse & Classify Stack Frames

Extract all stack frames and classify each one using the categories below.

#### Frame Classification

**CPython Runtime** — the Python interpreter itself:
- `.so` name: `python3.X`, `libpython3.X.so`, `libpython3.X.so.1.0`
- Symbols: `_PyEval_EvalFrameDefault`, `PyGILState_Ensure`, `take_gil`, `new_threadstate`,
  `PyEval_RestoreThread`, `PyEval_SaveThread`, `Py_RunMain`, `PyObject_Call*`, `_Py_*`,
  `PyThread_exit_thread`, `_PyRuntime`, `_PyThreadState_*`

**dd-trace-py Native (_threads)** — periodic thread / GIL management:
- `.so` name: `_threads.cpython-*.so`
- Symbols: `PeriodicThread_start`, `PeriodicThread_stop`, `PeriodicThread_join`,
  `PeriodicThread__periodic`, `PeriodicThread__before_fork`, `PeriodicThread__after_fork`,
  `PeriodicThread__on_shutdown`, `PeriodicThread_awake`, `PeriodicThread_dealloc`,
  `GILGuard`, `AllowThreads`, `PyRef`, `execute_native_thread_routine`

**dd-trace-py Native (_native / Rust)** — crashtracker, data pipeline, tracer utilities:
- `.so` name: `_native.cpython-*.so` (under `ddtrace/internal/native/`)
- Symbols: `crashtracker_*`, `TraceExporterPy::send`, `pyo3::*`, `rust_begin_unwind`,
  `rust_panic`, `libdd_crashtracker::*`, `datadog_profiling_ffi::*`, `ffe::*`, `ddsketch::*`

**dd-trace-py Native (_memalloc)** — memory profiler:
- `.so` name: `_memalloc.cpython-*.so`
- Symbols: `memalloc_*`, `Datadog::Sample*` (when called from memalloc context)

**dd-trace-py Native (_ddup / _stack / libdd_wrapper)** — profiling upload/sampling:
- `.so` names: `_ddup.cpython-*.so`, `_stack.cpython-*.so`, `libdd_wrapper.cpython-*.so`
- Symbols: `ddup_*`, `Datadog::Profile*`, `Datadog::Sample*`, `Datadog::Uploader*`,
  `Datadog::Sampler*`, `Datadog::EchionSampler*`, `Datadog::StackRenderer*`, `echion_*`

**dd-trace-py IAST/AppSec Native** — security instrumentation:
- `.so` name: `_native.cpython-*.so` (under `ddtrace/appsec/`), `_stacktrace.cpython-*.so`
- Symbols: `taint_*`, `TaintEngine*`, `tainted_ops::*`

**dd-trace-py Cython** — Python-level profiling helpers:
- `.so` names: `_threading.cpython-*.so`, `_exception.cpython-*.so`, `_sampler.cpython-*.so`,
  `_lock.cpython-*.so`, `_task.cpython-*.so`, `_encoding.cpython-*.so`
- Symbols: Cython-generated (often `__pyx_*` prefixes or module function names)

**Third-Party Native (NOT dd-trace-py)** — known third-party libs:
- `libev-*.so` / `libev.so.4` — gevent's embedded libev (NOT dd-trace-py)
- `_native__lib.cpython-*.so` — Pyroscope's native extension (path contains `pyroscope/`)
  **Note:** Pyroscope crashes appear in dd-trace-py crash telemetry because the crashtracker
  captures all process-level crashes. `_native__lib` (double underscore, in `pyroscope/`) is
  ALWAYS Pyroscope, never dd-trace-py. dd-trace-py's module is `_native` (single underscore,
  in `ddtrace/internal/native/`).
- `anyhow::error::object_drop`, `py_spy::*`, `goblin::*` — Pyroscope/py-spy Rust code
- Customer application code, third-party Python extensions

**OS/libc** — system libraries:
- `libc.so.6`, `libpthread.so`, `libm.so`
- Symbols: `gsignal`, `abort`, `pthread_exit`, `pthread_mutex_lock`, `__clone3`, `start_thread`,
  `cfree`, `free`, `malloc`

Create a classification table:

| # | Type | Symbol / Location | Description |
|---|------|------------------|-------------|
| 0 | {type} | {symbol} | {brief description} |
| ... | | | |

---

### Phase 2: Locate dd-trace-py Source Code

For each dd-trace-py frame that can be mapped to source code:

1. **Map `.so` name to source file**:
   - `_threads.cpython-*.so` → `ddtrace/internal/_threads.cpp`
   - `_native.cpython-*.so` (in `ddtrace/internal/native/`) → `src/native/` (Rust)
   - `_memalloc.cpython-*.so` → `ddtrace/profiling/collector/_memalloc.cpp`
   - `_ddup.cpython-*.so` → `ddtrace/internal/datadog/profiling/ddup/`
   - `_stack.cpython-*.so` → `ddtrace/internal/datadog/profiling/stack/`
   - `libdd_wrapper.cpython-*.so` → `ddtrace/internal/datadog/profiling/dd_wrapper/`

2. **Find the function** in the source file using Glob/Bash grep.

3. **Read code with context**: find the function body, read 10-15 lines before the relevant
   line, mark the crash point, read 5-10 lines after. Show enough to understand what the code is
   doing.

Format:
```
### Frame N: {symbol} ([{file}:{line}](GitHub link))

​```cpp
// {file}:{start}-{end}
{code with relevant line marked: // >>> CRASH POINT <<<}
​```

**Analysis**: {what this code does and why it may have failed}
```

---

### Phase 3: Apply General Crash Heuristics

dd-trace-py maintains two complementary native code safety references:

- `docs/native-code-review.md` — full incident-derived rules with historical PR links (10 sections)
- `.cursor/rules/native-code.mdc` — compact triage checklist with trigger symbols and stop conditions

Read **both files now**. Use their framework to classify the crash. The categories below
summarize the key stack-trace signals; refer to the docs for full rationale and PR history.

#### 3.1 — GIL Lifecycle During Finalization

**Stack signals**: `PyGILState_Ensure`, `PyEval_RestoreThread`, `take_gil`, `new_threadstate`,
`PyThread_exit_thread`, `pthread_exit`, `abort`, `gsignal`, `__forced_unwind`,
`abi::__cxa_throw`, `std::terminate` — especially combined with thread-entry frames like
`execute_native_thread_routine` or Rust `extern "C"` boundaries.

**What to check**:
- Is there a `py_is_finalizing()` / `is_finalizing()` check immediately before each GIL
  acquire/restore call? These checks are inherently TOCTTOU — finalization can begin in the
  window between the check and the call.
- Does the crash involve C++ RAII unwinding? `pthread_exit` on glibc throws
  `abi::__forced_unwind`; any C++ destructor or `catch(...)` that does not re-throw it will
  cause `std::terminate` → `SIGABRT`. On musl (Alpine Linux), `pthread_exit` uses `longjmp`
  instead — `catch(abi::__forced_unwind&)` never fires.
- For Rust/pyo3 code: `__forced_unwind` cannot propagate through `extern "C"` boundaries.
  Rust code that calls `PyEval_RestoreThread` (or uses `py.detach()`) needs its own
  finalization check — the C++ caller's `try/catch` cannot protect it.

**CPython version behavior** (from `docs/native-code-review.md`):

| CPython | Behavior when `take_gil()` detects finalization |
|---------|------------------------------------------------|
| 3.12 (all) | `PyThread_exit_thread()` → `pthread_exit()` → crash |
| 3.13.0–3.13.7 | Same as 3.12 |
| 3.13.8+ | `PyThread_hang_thread()` → `pause()` forever (hang, not crash) |
| 3.14+ | Hangs; `PyGILState_Ensure` itself hangs safely in 3.15+ |

**Key source locations**: `ddtrace/internal/_threads.cpp` (`GILGuard`, `AllowThreads`,
`PeriodicThread_start`), `ddtrace/internal/threads.py` (atexit handler),
`src/native/data_pipeline/mod.rs` (Rust pyo3 GIL release/restore).

#### 3.2 — Fork Safety

**Stack signals**: `pthread_mutex_lock`, `pthread_cond_wait`, `__lll_lock_wait` deep in a
thread that recently went through `_after_fork`, `__clone3` / `start_thread` in a crash that
occurs shortly after a fork event.

**What to check**:
- Any lock, mutex, or condition variable held by a parent thread is in an undefined state in
  the child after `fork()`. Child path must **recreate** primitives; parent path must
  **clear** them (other threads still hold references).
- Were all periodic threads stopped and joined before the fork? Was `_before_fork()` called?
- Did a thread fail to stop (blocked in I/O) and get inherited into the child while still
  holding a lock?

**Key source locations**: `ddtrace/internal/_threads.cpp` (`_before_fork`, `_after_fork`,
`safe_reset_thread`), `ddtrace/internal/threads.py` (`_before_fork`, `_after_fork_child`).

#### 3.3 — Object Lifetime Across Thread Boundaries (Use-After-Free)

**Stack signals**: SIGSEGV or SIGBUS at a CPython refcount operation (`Py_INCREF`,
`Py_DECREF`, `_Py_Dealloc`), or inside a `PyObject*` dereference inside a native thread
shortly after the thread started or while it was shutting down.

**What to check**:
- Was `Py_INCREF` called *before* `std::thread` creation? If it happens inside the lambda,
  there is a window where the object can be freed before the thread increments the refcount.
- Is `Py_DECREF` called after the thread has signaled completion (e.g., after
  `stopped_event->set()`)? On CPython 3.14+, `_Py_Dealloc` dereferences `tstate`
  immediately — `Py_DECREF` with a NULL tstate (during finalization) crashes.
- Does `PyRef` correctly defer the decrement until outside the finalization window?

**Key source locations**: `ddtrace/internal/_threads.cpp`

#### 3.4 — Memory Safety in Allocator Hooks

**Stack signals**: Crash inside `_memalloc.cpython-*.so` with CPython allocation functions
(`PyMem_Malloc`, `PyObject_Malloc`) or frame-walking functions (`PyThreadState_GetFrame`,
`PyFrame_GetBack`, `PyFrame_GetCode`, `PyUnicode_AsUTF8AndSize`) in the trace — especially
if the crash is a heap corruption SIGABRT or a SIGSEGV inside a seemingly unrelated function.

**What to check**:
- Is the crashing code executing inside a `malloc`/`free`/`realloc` hook? Any Python C API
  call that can allocate or trigger GC is unsafe there — it causes reentrant hook calls that
  corrupt internal state.
- Safe functions inside hooks: `PyThread_get_thread_ident()`, `Py_INCREF`, direct struct
  field reads. Unsafe: `Py_DECREF`, `PyFrame_GetBack()`, `PyUnicode_AsUTF8AndSize()`,
  `PyObject_CallObject()`.
- Is GC explicitly disabled? Verify it is actually compiled in (a previous fix was silently
  dead code due to a missing `#include`).

**Key source locations**: `ddtrace/profiling/collector/_memalloc.cpp`,
`ddtrace/profiling/collector/_memalloc_tb.cpp`

#### 3.5 — FFI Exception and Unwind Propagation

**Stack signals**: `rust_begin_unwind`, `rust_panic`, `__rust_start_panic` in frames from
`_native.cpython-*.so`; or `abi::__cxa_throw` / `__cxa_rethrow` crossing between `.so`
files; or `std::terminate` called from inside Rust code.

**What to check**:
- `abi::__forced_unwind` (from `pthread_exit`) cannot propagate through Rust `extern "C"`
  boundaries — Rust aborts on foreign exceptions.
- Does any Rust function call `PyEval_RestoreThread` or use `py.detach()` without a
  finalization check? It needs its own guard; the surrounding C++ `try/catch` cannot help.
- Does a C++ `catch(...)` exist that swallows `__forced_unwind` without re-throwing? That
  calls `std::terminate`.
- Does a native function call `getenv()` or (in Rust) `std::env::var()`? These are not
  thread-safe when another thread modifies the environment concurrently — can cause SIGSEGV.

**Key source locations**: `src/native/data_pipeline/mod.rs`, `ddtrace/internal/_threads.cpp`
(thread lambda catch blocks).

#### 3.6 — Unbounded Loops and Recursion

**Stack signals**: Deep or repeating identical frames (same function appearing many times),
deadlock-style hangs with a lock held (visible in thread dumps as `pthread_mutex_lock` with
no progress), or `SIGABRT` from `std::terminate` after a recursive call depth is exceeded.

**What to check**:
- Is there a loop over a CPython internal linked list (interpreters, threads, greenlets) with
  no upper bound? Corrupted or cyclic structures cause infinite loops.
- Can any function called from instrumentation code acquire a lock that is also instrumented
  (e.g., a lock profiler that logs, logging that acquires a lock)?
- Is there an explicit iteration limit?

**Key source locations**: Any loop over `interp->next`, greenlet chains, or stack chunks in
`ddtrace/internal/datadog/profiling/stack/`.

#### 3.7 — Cython Silent Exception Swallowing

**Stack signals**: Incorrect/unexpected behavior rather than a hard crash; or a crash inside
a Cython-generated `.so` (`__pyx_*` frames) where a NULL pointer is dereferenced after a
function returned 0/NULL with no exception set.

**What to check**:
- Does every `cdef` function that can raise declare `except *`, `except -1`, or `except? -1`?
  Without this, exceptions are silently discarded and the caller proceeds with corrupt state.
- Do `nogil` blocks avoid all Python C API calls and `PyObject*` access? Only pure C/C++
  operations are safe inside `nogil` — any Python API call there is undefined behaviour.
- Are `PyUnicode_AsUTF8AndSize` results stored as `string_view` / raw `const char*` while
  the source Python object may have been garbage collected?

**Key source locations**: `ddtrace/profiling/collector/_exception.pyx`,
`ddtrace/profiling/collector/_lock.pyx`, `ddtrace/internal/_encoding.pyx`.

#### 3.8 — Stripped Binary Misattribution

**Stack signals**: Symbol names that don't match the `.so` they appear in, or a crash at an
address that doesn't correspond to any named symbol in the frame (address-only frames at the
top of a crashing `.so`).

**What to check**:
- In stripped release builds, the topmost named frame may be the *next* exported symbol
  *after* the real crash site, not the function that actually crashed. The true crash may be
  in an inlined or stripped function above it.
- When comparing a crash address against source, verify against the pinned library version
  (`src/native/Cargo.toml` for libdatadog, `setup.py` for libddwaf) — not the latest. API
  and symbol layouts differ between versions.
- If the reported function seems inconsistent with the rest of the stack, treat it as
  approximate and look at the surrounding frames for the true call site.

---

### Phase 4: Attribute the Crash

Now that you have investigated the source code (Phase 2) and matched against known patterns
(Phase 3), determine attribution: **Is this a dd-trace-py bug?**

Use these signals:

1. **Frame ownership**: Are any dd-trace-py native frames in the crashing thread? If the only
   dd-trace-py frames are in other threads (not the crashing one), or absent entirely,
   dd-trace-py may not be the root cause.

2. **Known third-party library**:
   - `_native__lib.cpython-*.so` in a `pyroscope/` path → Pyroscope bug
   - `libev-*.so` in a gevent-using app → gevent/libev interaction
   - Customer `.so` (e.g., `pycbc`, `grpc`) → third-party extension bug

3. **Pattern match from Phase 3**: Did the crash match a known dd-trace-py pattern?

4. **Code evidence from Phase 2**: Does the source code reveal a bug in dd-trace-py, or does
   it show dd-trace-py code operating correctly while a third-party or CPython component fails?

5. **Web search for upstream issues**: If the crash appears to involve CPython internals or a
   third-party library, use `WebSearch` to check whether this is a known upstream bug:
   - Search for the crashing symbol + "crash" or "segfault" + the library/CPython version
   - Check the CPython issue tracker (`github.com/python/cpython/issues`)
   - Check the third-party library's issue tracker
   - Search for related GitHub issues in `DataDog/dd-trace-py`

   Example searches:
   - `"take_gil" crash CPython 3.12 pthread_exit site:github.com/python/cpython`
   - `PyThread_exit_thread finalization crash cpython issue`
   - `site:github.com/DataDog/dd-trace-py/issues SIGSEGV _memalloc`

State the attribution clearly: **dd-trace-py bug**, **third-party bug** (name the library),
**CPython bug** (cite the upstream issue if found), **interaction/race between components**,
or **unknown**.

---

### Phase 5: Reconstruct the Crash Flow

Build a narrative explaining the execution flow leading to the crash:

1. **Thread identity**: What kind of thread is this? (main thread, PeriodicThread, OS thread)
2. **Entry point**: Where did execution start? (thread start function, signal handler, etc.)
3. **Key operations**: What was the thread trying to do?
4. **Critical transitions**: Where did control flow between components?
5. **Failure point**: Where and why did it crash?
6. **Crash type**: null dereference, use-after-free, race condition (TOCTTOU), `abort` via
   `pthread_exit`/C++ unwinding, double-free, assertion failure, etc.

Write this as a clear step-by-step narrative. Focus on HOW the crash happened based on the
stack trace and code evidence. Do not prescribe specific fixes.

---

### Phase 6: Find Related Code and History

Use Bash + git to find context. When `GIT_REF` is not `main`, scope history queries to that
ref so results reflect the code that actually shipped in the crashing binary.

1. Recent commits to affected files (up to the crashing revision):
   `git log --oneline -10 {GIT_REF} -- {file}`
2. Search for relevant changes:
   `git log --grep="GIL" --grep="finalize" --grep="crash" --oneline -20 {GIT_REF}`
   (use keywords relevant to the crash area)
3. For each relevant commit, extract PR numbers from the message (e.g., `(#7659)`) and link:
   `https://github.com/DataDog/dd-trace-py/pull/{number}`
4. Grep for `AIDEV-` comments near the crash site: `grep -n "AIDEV" {file}` — these are
   embedded rationale notes left specifically for AI and developers explaining non-obvious
   invariants. Read any that are near relevant functions.

**Prioritize PR links** — PRs have descriptions and discussion context that commit messages lack.
The historical examples in `docs/native-code-review.md` also contain PR links for past crashes
of each type — cross-reference them when a heuristic from Phase 3 matches.

---

## Output Format

```markdown
# Crash Analysis Report
**Generated**: {ISO 8601 timestamp}
**Source ref**: [`{GIT_REF}`](https://github.com/DataDog/dd-trace-py/tree/{GIT_REF}) {— reason, e.g. "from crash metadata v2.18.0" or "fallback to main"}

## Crash Metadata
{If fetched via UUID, include this section. Omit if the user pasted a raw stack trace.}

| Field | Value |
|-------|-------|
| Tracer version | {tracer_version} |
| Runtime version | {runtime_version} |
| Service | {service} |
| Platform / Arch | {platform} / {architecture} |
| Signal | {signal, e.g. SIGSEGV, SIGABRT} |
| Org ID | {org_id} |
| Log link | [{log_id}]({log_url}) |

## Executive Summary
{2-3 sentences: what crashed, which component, what the crashing thread was doing.
De-mystify the hex addresses — translate them to human-readable description of the crash.}

## Stack Trace Classification

| # | Type | Symbol / Location | Description |
|---|------|------------------|-------------|
| 0 | {type} | {symbol} | {brief desc} |
| ... | | | |

{Note any other threads if provided, but focus on the crashed thread.}

## Code Context

{For each critical dd-trace-py frame:}

### Frame N: {symbol} ([{file}:{line}](GitHub link))

​```cpp
// {file}:{start}-{end}
{code with // >>> CRASH POINT <<< marker}
​```

**Analysis**: {what the code does and why it crashed}

## Pattern Match
{If a known pattern from Phase 3 matched, name it and explain why.
If no known pattern matched, say so.}

## Attribution
**{dd-trace-py bug | third-party bug ({library name}) | CPython bug ({link to issue if found}) | interaction/race | unknown}**

{2-3 sentence justification referencing evidence from code investigation, pattern matching,
and web search results. Explain WHY this attribution was chosen.}

## Crash Flow Reconstruction

{Step-by-step narrative}

**Crash type**: {e.g., TOCTTOU race → null dereference in new_threadstate}

**How it happened**: {clear explanation based on stack trace + code}

## Related Code

**Relevant PRs and commits**:
- [#{number}](https://github.com/DataDog/dd-trace-py/pull/{number}): {title} — {why relevant}

**Upstream issues** (if any):
- [{issue title}]({url}) — {relevance}

**Related code locations**:
- [{file}:{line}](GitHub link) — {description}

## Additional Context
{Environment details, Python version, platform, feature flags in use, or other relevant background.}

---
*Analysis generated by Claude Code /analyze-crash*
*This analysis is intended for triage. Engineers should review before acting on it.*
```

---

## Output File Management

1. **Create output directory**: `mkdir -p ~/.claude/analysis && echo ~/.claude/analysis`
2. **Generate filename**: `crash-analysis-{YYYYMMDD-HHMMSS}.md`
3. **Save file**: Write the markdown analysis to the file
4. **Tell the user** the full path where the analysis was saved

---

## Important Guidelines

- **Investigate before attributing**: The crashtracker captures all process crashes — many will
  be in third-party code (Pyroscope, customer extensions, gevent). Investigate the code and
  apply heuristics (Phases 2-3) before making the attribution call (Phase 4). The evidence
  you gather significantly improves attribution accuracy.
- **Native frames are opaque**: Stack frames often have only a hex address and symbol name, no
  file:line. Use the symbol name + `.so` name to classify and find source.
- **`_native__lib` vs `_native`**: `_native__lib` (double underscore, in `pyroscope/`) is ALWAYS
  Pyroscope. `_native` (single underscore, in `ddtrace/internal/native/`) is dd-trace-py. Never
  confuse them.
- **Focus on triage and understanding**: Explain HOW the crash happened; do not prescribe fixes.
- **Describe crash types**: Identify the category (null deref, race, abort, use-after-free) but
  stop before suggesting specific code changes.
- **Continue with missing info**: If a symbol can't be found in source, note it and continue.
- **Mark uncertainties**: If something is speculative, say so explicitly.
- **Always include GitHub links**: Every file:line reference should be a clickable link using the
  resolved `GIT_REF` — never hard-code `main` unless it is the resolved ref.
- **Prefer PR links over commits**: Extract `(#NNNN)` from commit messages and link to the PR.

## Now Analyze

Obtain the crash data (fetch by UUID or parse the pasted log), resolve the source revision,
and follow the workflow above to generate a comprehensive crash analysis.
