# PoC: Real-Time Instrumentation of Python Code via Bytecode Patching

## Context / Motivation

We want a small proof of concept to demonstrate **runtime (in-memory) instrumentation** of Python functions and class methods by **patching bytecode / code objects** while the program is running.

This PoC is conceptually similar to the existing debugging/instrumentation approach in:

- `.cursor/rules/debugging.mdc`
- `ddtrace/debugging`

**High-level idea:** given a target symbol (function or method) identified by a string path at runtime, we can:

1. Resolve it to a Python callable in the running process.
2. Generate a modified version of its `code` (bytecode) that injects a tiny instrumentation hook.
3. Replace the original callable in memory (e.g., `function.__code__ = new_code` or patching the class attribute for methods).
4. Observe the impact immediately for subsequent calls.

> NOTE: This PoC is about dynamic, runtime patching — not import-time decoration and not a static AST rewrite at build time.

---

## Goal

Create two cooperating components:

1. A target application (“instrumenting” app) that continuously calls many dummy functions and methods.
2. A terminal UI / controller that:
   - Displays a live table of all instrumentable callables.
   - Tracks:
     - Total call count per callable.
     - Instrumentation-hit count per callable.
   - Accepts real-time user input to instrument a callable by path.
   - Applies instrumentation in the background without stopping the app loop.

---

## Functional Requirements

### 1) Instrumenting Target Application

The target app should include:

- Many dummy module-level functions.
- Many dummy classes with instance methods, and optionally some classmethod / staticmethod.
- Each function or method should be intentionally trivial and only increment a call counter.

The app must:

- Run a loop indefinitely.
- On each iteration, randomly pick a callable from a known registry and invoke it.
- Optionally sleep briefly to keep the UI readable.

---

### 2) Live Terminal UI / Controller

The terminal UI must:

- Display a table with:
  - Qualified name
  - Call count
  - Instrumentation count
- Update continuously.
- Accept user input identifying a callable to instrument.

Recommended input format:

- `module.submodule:func`
- `module.submodule:Class.method`

Upon input:

- A worker thread instruments the target callable.
- The main loop continues uninterrupted.
- Future calls execute the patched bytecode.

---

## Instrumentation Strategy

Instrumentation injects a hook equivalent to:

```python
instrumentation_hook("qualified.name")
```

This must be done via bytecode or code-object rewriting, not via Python wrappers.

---

## Bytecode Notes

- Bytecode differs across Python versions.
- Python 3.11 introduces major opcode changes.

For this PoC, pin to a single Python version.

---

## Symbol Resolution

1. Import the module.
2. Resolve attributes.
3. Validate instrumentability.
4. Apply patch.

Nested functions are out of scope.

---

## State Management

Maintain shared, thread-safe state:

- call_count
- instrumentation_count
- original_code
- is_instrumented

---

## Concurrency Model

- Main thread: execution loop + UI refresh.
- Input thread: reads user commands.
- Worker thread: applies instrumentation.

In-flight calls are not retroactively modified.

---

## Acceptance Criteria

- Live call counts update correctly.
- Instrumentation can be applied at runtime.
- Instrumented functions increment instrumentation counters.
- The application never restarts or pauses.

---

## Example

User inputs:

```
instrumenting_app.dummy_modules.mod_a:func_7
```

UI confirms patch and instrumentation count increases.

