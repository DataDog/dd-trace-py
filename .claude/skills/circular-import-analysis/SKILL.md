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
cycles as introduced by the current task. Match CI by using the candidate's `cycles.py` for both
analyses, passing each checkout's `ddtrace/` directory through `--root` (the base is the merge base).
Use its `compare <base.json> <candidate.json>` command to compare the outputs.

Trace the exact imported names in both directions. Prefer moving code to its owner, extracting
shared types, or using a registration/event interface. Choose the smallest change that restores
clear ownership; do not add abstractions simply to satisfy a generic pattern.

Choose the repair that matches the names crossing the boundary:

- **Shared types/constants:** Extract only the common contract into a module that imports neither
  implementation; both callers import it. `ddtrace/_trace/types.py` is a small local example,
  not a universal destination for shared types.
- **Dependency inversion:** Put a `Protocol` or callback contract at the lower-level boundary;
  let the higher-level owner supply the implementation. `Product` and `ProductManager` in
  `ddtrace/internal/products.py` illustrate a manager calling a lifecycle contract.
- **Startup registration:** If the lower-level module imports its owner only to register itself,
  expose an initializer and call it from the existing startup owner once dependencies are ready.
  Preserve enablement, ordering, and repeated-initialization behavior.
- **Split a mixed module:** Separate low-level types/primitives from high-level orchestration.
  Have both the orchestrator and outside consumers import the primitive module, which must not
  import back through the orchestrator or a re-export that recreates the cycle.
- **Move to the owner:** If a helper already belongs to one side, move it there and update callers
  instead of introducing another abstraction. Preserve public import paths and behavior.

For an enabled startup path, follow `ddtrace/internal/aiguard/product.py` through
`ddtrace/aiguard/_initialization.py`. This is not a blanket ban on automatic registration:
`ddtrace/internal/core/subscriber.py` deliberately registers some subscribers at class definition.

Do not hide a dependency cycle with function-local imports or `TYPE_CHECKING` guards.
This does not ban deliberate lazy imports for startup or gevent compatibility unrelated to a cycle.
Rerun the analysis after the fix. For foundation/product imports, also use
[dependency-direction-analysis](../dependency-direction-analysis/SKILL.md).
