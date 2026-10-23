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

# Circular imports

Use when adding/moving modules, changing import relationships, or diagnosing new cycles in CI.
Run from the repository root, writing analysis output to a temporary location:

```bash
uv run --script scripts/import-analysis/cycles.py analyze <output.json>
```

Each result contains the modules in a tangled component (`nodes`) and a concrete cycle (`cycle`).
Compare with the base revision when determining whether a cycle is new; do not treat all existing
cycles as introduced by the current task. See the detector and its CI invocation for comparison semantics.

Trace the exact imported names in both directions. Prefer moving code to its owner, extracting
shared types, or using a registration/event interface. Choose the smallest change that restores
clear ownership; do not add abstractions simply to satisfy a generic pattern.

Do not hide a dependency cycle with function-local imports or `TYPE_CHECKING` guards.
This does not ban deliberate lazy imports for startup or gevent compatibility unrelated to a cycle.
Rerun the analysis after the fix. For foundation/product imports, also use
[dependency-direction-analysis](../dependency-direction-analysis/SKILL.md).
