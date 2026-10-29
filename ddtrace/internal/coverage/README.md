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

- **Callback/registry execution** (stored function references) can execute a workspace
  file outside the anchor's import closure without executing an import. Import-only
  mode has no signal for this — no import statement fires. The opt-in conservative
  flag (see below) addresses this by attributing all workspace modules to every test.
- An `__import__` or `import_module` alias saved before installation bypasses the
  wrapper, but the module watchdog still sees the first load and `on_module_body_finished`
  attributes the module file to the current test context. The module's own import-time
  dependencies remain unreachable (no caller edge), but the module itself is covered.
- Custom loading through `exec`, `runpy`, or loader APIs bypasses all import machinery
  and is completely invisible to import-only mode.
- **Extra dependencies cause unnecessary test runs.** Every test in a file inherits
  its module-level imports, even when only another test uses them. Session-level
  lazy imports and conftest anchors are also conservative. This trades selection
  precision for protection against missing shared dependencies.
- New files that make previously failing optional imports succeed, resource reads,
  environment configuration, and external services need safeguards beyond an
  import graph. An import-only result should not establish that a test is safe
  to skip by itself.

### Conditional imports in backfill (fixed)

Backfill walks the full AST (`ast.walk`), not just top-level statements. Imports
in `if`, `try`, loops, class bodies, and function bodies are all discovered. Each
name is verified against `sys.modules`: only imports that actually executed (the
branch was taken, the function was called during the module's own load) create
dependency edges. Dead branches are excluded — no over-attribution.

Example: `if sys.platform == "darwin": from mac_helper import fn` — the edge is
recorded only if `mac_helper` is in `sys.modules` at backfill time (i.e., we're on
macOS and the branch ran). On Linux, `mac_helper` is not in `sys.modules`, the edge
is not created, and the test is not falsely impacted.

### Conservative mode (opt-in flag)

`_DD_COVERAGE_IMPORT_ONLY_CONSERVATIVE=1` addresses the callback/registry blind spot
by attributing every workspace module (pre-imported and dynamically loaded) and its
transitive import-time dependencies to every test.

This is **more conservative than PY_START**: PY_START only covers files whose code
actually executed during the test. Conservative mode covers every workspace module
regardless of whether its code ran. This means:

| | PY_START (instrumented) | Import-only (default) | Import-only (conservative) |
|---|---|---|---|
| Files imported by the test | covered (PY_START) | covered (closure) | covered (closure) |
| Files imported by conftest | covered (PY_START) | covered (conftest anchor) | covered (conftest anchor) |
| Files called via stored references | covered (PY_START) | **MISSING** | covered (workspace roots) |
| Files whose code never ran | **NOT covered** | **NOT covered** | **covered** (over-attribution) |
| Files in sys.modules but never used | **NOT covered** | **NOT covered** | **covered** (over-attribution) |

Concrete over-attribution example: if workspace modules A, B, C are all
pre-imported at collection time, but test_1 only uses A:
- Default: test_1 gets {A, A's deps}
- Conservative: test_1 gets {A, A's deps, B, B's deps, C, C's deps}
- A change to B's code marks test_1 as impacted in conservative mode (it shouldn't be)

This trades skippability for safety: any workspace file change impacts all tests.
In a workspace with many modules, conservative mode reduces skipping to near zero.
Enable it only during pilot evaluation or when the callback/registry pattern is
known to be common in the codebase.

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
