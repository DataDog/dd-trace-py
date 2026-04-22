---

name: circular-import-analysis
description: >
  Run circular import detection against ddtrace and propose architectural fixes
  for any cycles found. Use this when adding or refactoring modules, or when the
  detect_circular_imports CI job reports new cycles on a PR.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - TodoWrite
---

# Circular Import Analysis Skill

This skill runs the circular import detector locally and proposes sound architectural
fixes for any cycles found. The guiding principle is **Separation of Concerns**: fixes
must restructure ownership of code, not paper over the problem with deferred imports.

## When to Use This Skill

- The `detect_circular_imports` CI job reports new cycles on your PR.
- You are adding a new module or moving code between modules and want to check for cycles upfront.
- You are refactoring and want to verify you haven't introduced cycles.

## Running the Analysis

```bash
uv run --script scripts/import-analysis/cycles.py analyze cycles.json
```

This writes all detected cycles to `cycles.json` and prints a count to stdout. Requires
`uv` on `PATH` (`brew install uv` or `pip install uv`).

To read the results:

```bash
cat cycles.json   # raw JSON array of cycles
```

Each entry is an array of module names forming the cycle, e.g.:
```json
["ddtrace", "ddtrace.internal.datastreams", "ddtrace"]
```

Clean up afterwards:
```bash
rm cycles.json
```

## Architectural Patterns for Breaking Cycles

> **Never use deferred imports (`import x` inside a function body) as a fix.** They hide
> the structural problem, complicate testing and static analysis, and impose a runtime
> cost on every call.

### Understand the cycle first

Before proposing a fix, trace **why** each module needs the other:

```bash
# Who imports whom?
grep -rn "^import ddtrace\|^from ddtrace" ddtrace/internal/datastreams/ --include="*.py"
grep -rn "^import ddtrace.internal.datastreams\|^from ddtrace.internal.datastreams" ddtrace/ --include="*.py"
```

Identify the exact names (classes, functions, constants) that cross the boundary in each
direction. Often only a small fraction of each module is actually involved.

---

### Pattern 1 — Extract shared types / interfaces into a third module

**When to use:** Two modules share a type (e.g. a dataclass, a Protocol, a constant) that
both need to import, but neither should own.

**Before:**
```
ddtrace.foo  →  ddtrace.bar  →  ddtrace.foo   (cycle)
```

**After:**
```
ddtrace.foo  →  ddtrace._types    (no cycle)
ddtrace.bar  →  ddtrace._types
```

Create a thin `_types.py` (or `interfaces.py`) module that contains only the shared
contract. Both sides import from it; neither imports from the other. In dd-trace-py,
`ddtrace/_trace/types.py` and `ddtrace/internal/schema.py` are examples of this pattern.

---

### Pattern 2 — Dependency inversion (depend on an abstraction, not the concrete module)

**When to use:** Module A calls into module B, but B also needs to notify A of events.

**Before:**
```
ddtrace.core  →  ddtrace.contrib.foo  →  ddtrace.core
```

**After:**
```
ddtrace.core    →  ddtrace._interfaces.IFooHook  (abstract)
ddtrace.contrib.foo  →  ddtrace._interfaces.IFooHook  (implements)
```

Define a `Protocol` or abstract base in a third module. `ddtrace.core` depends on the
protocol, not the implementation. The contrib module registers itself at startup
(see the existing `ddtrace/internal/hooks.py` registration pattern).

---

### Pattern 3 — Push initialisation to the importer (registry / lazy registration)

**When to use:** A lower-level module (e.g. `ddtrace.internal.X`) imports a higher-level
module only to register or configure itself at import time.

**Before:**
```
ddtrace  →  ddtrace.internal.X  →  ddtrace   (X registers itself during import)
```

**After:** Remove the registration from `ddtrace.internal.X`'s module scope. Instead,
have `ddtrace/__init__.py` (the higher-level module) call `X.register()` explicitly after
importing `X`. The lower-level module exposes a registration API but does not call it
itself.

This is the standard dd-trace-py pattern: integrations do not self-activate; `ddtrace`
drives the lifecycle.

---

### Pattern 4 — Split a module along its dependency boundary

**When to use:** A large module contains both high-level logic (which imports from
elsewhere) and low-level primitives (which are imported by elsewhere). The primitives do
not actually need the high-level logic.

**Before:**
```
ddtrace.trace  →  ddtrace._trace.tracer  →  ddtrace.trace
```

**After:**
```
ddtrace.trace._api   (pure public types / constants — no upward imports)
ddtrace._trace.tracer  →  ddtrace.trace._api
ddtrace.trace          →  ddtrace.trace._api
                       →  ddtrace._trace.tracer
```

Check whether the parts of the module that are imported by the lower-level module can be
split into a `_api.py`, `_types.py`, or `_base.py` sub-module with no reverse
dependencies.

---

### Pattern 5 — Move the code to the module that owns it

**When to use:** The cycle exists because a piece of logic ended up in the wrong module.
This is the simplest and often best fix.

Ask: "Does this function/class conceptually belong in module A or module B?" If it
belongs in A, move it there so that B (which uses it) imports from A — not the other way
round. The cycle disappears because there is now a clear dependency direction.

---

## Worked example: the current ddtrace cycles

```
ddtrace -> ddtrace.internal.datastreams -> ddtrace
ddtrace.trace -> ddtrace._trace.tracer -> ddtrace.internal.datastreams -> ddtrace.trace
ddtrace -> ddtrace.trace -> ddtrace._trace.tracer -> ddtrace.internal.datastreams -> ddtrace
```

All three cycles pass through `ddtrace.internal.datastreams` importing something from the
top-level `ddtrace` or `ddtrace.trace` packages. The correct investigation path:

1. Find exactly what `ddtrace.internal.datastreams` imports from `ddtrace` / `ddtrace.trace`:
   ```bash
   grep -rn "^from ddtrace\b\|^import ddtrace\b" ddtrace/internal/datastreams/ --include="*.py"
   ```
2. Determine whether those names are low-level enough to live in `ddtrace/internal/` (→ Pattern 5),
   or whether they represent a shared contract (→ Pattern 1 or 2).
3. Propose the move or extraction; do not add `if TYPE_CHECKING` guards or function-level
   imports as a substitute.

## Decision checklist before proposing a fix

1. **Identify the exact cross-boundary names** — grep both directions.
2. **Classify the dependency:**
   - Shared data type / constant → Pattern 1 (extract)
   - Callback / notification → Pattern 2 (inversion)
   - Self-registration at import time → Pattern 3 (push to caller)
   - Mixed concerns in one file → Pattern 4 (split)
   - Wrong home → Pattern 5 (move)
3. **Prefer the fix with the fewest new files** — one move is better than a new `_types.py`
   if the type already conceptually belongs somewhere.
4. **Verify** by re-running `uv run --script scripts/import-analysis/cycles.py analyze cycles.json` after the change and confirming the cycle is gone.
