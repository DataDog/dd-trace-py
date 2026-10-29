# Coverage instrumentation modes

The coverage collector (`code.py`) delegates all mode-specific behavior through the
`CoverageMode` protocol (`mode.py`). Two implementations exist:

- **`InstrumentedCoverage`** (`instrumented_mode.py`) — the default. Instruments code
  objects via the version-specific ladder (`instrumentation.py`), recording per-line
  or per-file (PY_START) coverage depending on the `_DD_COVERAGE_FILE_LEVEL` flag.

- **`ImportOnlyCoverage`** (`import_only.py`) — opt-in via `_DD_COVERAGE_IMPORT_ONLY=1`.
  Collects file dependencies exclusively from import events (wrappers around
  `builtins.__import__` and `importlib.import_module`); no code object is
  instrumented. Works on every supported Python version (3.9–3.14).

## Import-only mode

File dependencies are collected from successful imports, including cached import
statements and calls through the wrapped `importlib.import_module`. The pytest
integration anchors each test file, ancestor package initializers, and ancestor
conftest files. Their transitive import-time dependencies are shared by tests;
imports executed inside a test context remain local to that context. Imports
outside a test context are treated as shared session dependencies.

### Known limitations

This is a dependency approximation, not equivalent to execution coverage:

- **Missed dependencies can cause unsafe skips.** Calls through injected callbacks,
  fixture/plugin objects, registries, or other references can execute a workspace
  file outside the anchor's import closure without executing an import. Such a
  file is invisible to this mode.
- An `__import__` or `import_module` alias saved before installation, or custom
  loading through `exec`, `runpy`, or loader APIs, bypasses import-event hooks. The
  module watchdog can still see some first loads; cached calls and their caller edges
  are not recovered. Install the collector as early as possible.
- For modules already loaded at installation, backfill reads only unconditional
  top-level import statements. Imports in `if`, `try`, loops, class bodies, and
  functions, and dynamic imports, are not reconstructed. Inspecting all AST
  imports would also attribute branches that never ran, so backfill deliberately
  does not claim to recover their runtime history.
- **Extra dependencies cause unnecessary test runs.** Every test in a file inherits
  its module-level imports, even when only another test uses them. Session-level
  lazy imports and conftest anchors are also conservative. This trades selection
  precision for protection against missing shared dependencies.
- New files that make previously failing optional imports succeed, resource reads,
  environment configuration, and external services need safeguards beyond an
  import graph. An import-only result should not establish that a test is safe
  to skip by itself.

When evaluating, compare per-test dependencies and skip decisions against
instrumented coverage on representative suites. Include collection-time fixtures,
parameterized dynamic imports, cached imports, preloaded packages, threads/copied
contexts, and third-party import wrappers. Check both missing files (unsafe skips)
and extra files (lost skipping opportunities); a lower coverage collection time
alone does not establish a safe replacement.

## Implementation boundary

`import_only.py` and `instrumented_mode.py` own their respective instrumentation,
context expansion, and lifecycle behavior. Neither imports the other or the
collector. `mode.py` contains the narrow collector contract and shared optional
lifecycle defaults; the collector only selects and delegates to a mode. Removing
either implementation requires removing its import and selection in `code.py`,
not moving its behavior into the remaining implementation.

## Cache boundaries and performance

Test-file path lookups use a bounded cache in the pytest plugin. Anchor discovery
is cached per file and collector installation in import-only mode, so tests in
the same file do not repeat package/conftest filesystem walks. Anchor files are
assumed to be stable during a test session; a new installation rediscovers them.

Name resolution and shared dependency closures are reused across test contexts.
A new test-local edge invalidates global reporting caches, but leaves those shared
caches intact. New shared edges still invalidate shared closures; newly registered
modules, aliases, and import-time coverage invalidate both modes' derived data.

The import hook caches dotted-name prefixes in a bounded 4,096-entry cache,
cleared on uninstall. It reads module objects and their `__file__` values on every
event so replacing or relocating a module does not stale the hook's file lookup.

The wrappers remain global, but raw file paths are classified once per
installation: one cache lookup returns either a canonical included path or an
exclusion. Simple imports from excluded callers return after checking their one
target, without prefix expansion or caller/edge bookkeeping. Dotted names and
fromlists still check each candidate, without building a temporary file list.

Caching complete test results by root files alone would be incorrect: two tests
with the same roots can execute different runtime imports. Any such cache would
also need the context's edges and a shared-graph version. Per-root shared closures
already retain up to O(roots x reachable files) path references for heavily
overlapping graphs. Measure peak memory as well as runtime on large suites before
adding another layer of closure memoization.
