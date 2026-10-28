# Import-only coverage experiment

`_DD_COVERAGE_IMPORT_ONLY=1` collects file dependencies from successful imports,
including cached import statements and calls through the wrapped
`importlib.import_module`. It does not instrument executed lines or functions.
The pytest integration anchors each test file, ancestor package initializers,
and ancestor conftest files. Their transitive import-time dependencies are shared
by tests; imports executed inside a test context remain local to that context.
Imports outside a test context are treated as shared session dependencies.

This is a dependency approximation, not equivalent to execution coverage:

- **Missed dependencies can cause unsafe skips.** Calls through injected callbacks,
  fixture/plugin objects, registries, or other references can execute a workspace
  file outside the anchor's import closure without executing an import. Such a
  file is invisible to this mode.
- An `__import__` or `import_module` alias saved before installation, or custom
  loading through `exec`, `runpy`, or loader APIs, bypasses import-event hooks. The module watchdog
  can still see some first loads; cached calls and their caller edges are not
  recovered. Install the collector as early as possible.
- For modules already loaded at installation, backfill reads only unconditional
  top-level import statements. Imports in `if`, `try`, loops, class bodies, and
  functions, and dynamic imports, are not reconstructed. Inspecting all AST
  imports would also attribute branches that never ran, so backfill deliberately
  does not claim to recover their runtime history.
- **Extra dependencies cause unnecessary test runs.** Every test in a file inherits
  its module-level imports, even when only another test uses them. Session-level
  lazy imports and conftest anchors are also conservative. This trades selection
  precision for protection against missing shared dependencies.
- New files that make previously failing optional imports succeed, resource
  reads, environment configuration, and external services need safeguards beyond an
  import graph. An import-only result should not establish that a test is safe to
  skip by itself.

When evaluating the experiment, compare per-test dependencies and skip decisions
against instrumented coverage on representative suites. Include collection-time
fixtures, parameterized dynamic imports, cached imports, preloaded packages,
threads/copied contexts, and third-party import wrappers. Check both missing
files (unsafe skips) and extra files (lost skipping opportunities); a lower
coverage collection time alone does not establish a safe replacement.
