# Coverage instrumentation modes

The collector (`code.py`) selects one implementation of `CoverageMode` (`mode.py`):

- **`InstrumentedCoverage`** (`instrumented_mode.py`) instruments code through the
  Python-version-specific ladder, collecting line or file coverage according to
  `_DD_COVERAGE_FILE_LEVEL`.
- **`ImportOnlyCoverage`** (`import_only.py`), enabled by
  `_DD_COVERAGE_IMPORT_ONLY=1`, collects file dependencies from successful import
  statements and `importlib.import_module` calls, including cached imports.
  It forces file-level coverage and import-time dependency collection without
  rewriting code or registering monitoring events.

The implementations import neither each other nor the collector. Removing either
requires changing its import and selection in `code.py`, not moving its behavior
into the other mode. The pytest integration supplies only the test file path;
anchor discovery, import hooks, and attribution policy belong to the mode.

## Attribution

Each test inherits its file, ancestor package initializers, ancestor conftests,
and their transitive import-time dependencies. Imports executed inside a test
context stay local to that context. Module-body imports and imports outside any
context are shared dependencies. Completed copied contexts cannot publish new
shared edges or borrow another test's collector.

### AST backfill

Modules imported before installation have no recorded import history. Backfill
registers their workspace files and scans the full AST, including conditional,
class-body, and function-local imports. Only candidates whose targets are present
and non-None in `sys.modules` become edges. Source is parsed as bytes to preserve
Python's source-encoding handling. Aliases are registered but do not repeat
parsing or change the module's package for relative-name resolution.

**This does not reconstruct execution history.** A target loaded by a different
importer can make an import inside a dead branch or an uncalled function appear
as a dependency. Conversely, a previously imported target removed from
`sys.modules` cannot be recovered this way. Backfill deliberately over-approximates
possible dependencies; ordinary post-installation events record successful
executed imports instead.

## Limits

Import-only coverage approximates dependencies, not execution:

- Calls through stored callbacks, fixtures, registries, or injected objects can
  execute a workspace file outside the anchor's closure without any import event.
- Import aliases captured before installation bypass the wrappers. The watchdog
  still observes normal first loads, but cached calls and their caller edges are
  invisible. Custom loaders and `exec` can also bypass module-load observation;
  any imports their code executes may still be observed by the wrappers.
- Shared module-level imports, conftest anchors, and AST backfill can attribute
  unused files and cause unnecessary test runs.
- New optional dependencies, resource reads, environment changes, and external
  services need safeguards beyond an import graph. A file that has never been
  imported is not made visible just because it exists in the workspace.

A full `sys.modules` snapshot would attribute other tests' loaded modules as well;
a before/after difference would miss cached imports. Import-event tracking keeps
that per-test distinction, but does not prove that skipping is safe.

Evaluate missing files and impacted-test decisions against instrumented coverage,
not collection speed alone. Include preloaded modules, dynamic and cached imports,
callbacks, copied contexts, threads, and third-party import wrappers.

## Benchmarks

`benchmarks/import_only` measures warmed import statements and per-test graph
expansion, checking expected attribution before timing. Setup, corpus generation,
and module loading are outside the timed region. Scenario-specific SLOs live in
`.gitlab/benchmarks/slos/ci-app-libraries.yml`; calibrate changes against repeated
measurements on representative benchmark runners.
`benchmarks/pytest_plugin` additionally measures whole-session startup and pytest
integration with no-coverage and instrumented controls. Run both through
`scripts/run-benchmarks` against an explicit source baseline, not a published
release lacking the experiment.

## Cache boundaries and performance

- The pytest plugin memoizes item-to-path lookup. Anchor discovery is cached per
  file and collector installation. Test files, conftests, and symlink layouts are
  assumed stable during a session; a new installation rediscovers anchors.
- Pure relative-name resolution, dotted-name prefixes, and import-name tuples use
  bounded 4,096-entry caches, cleared on uninstall. Module objects and `__file__`
  values are always read live, including excluded targets, so replacement or
  relocation does not stale the event lookup.
- Raw paths are classified once per installation. Simple imports from excluded
  callers still check their target, allowing frameworks to load workspace code.
- Name resolution and per-root shared closures survive test-local edges. New
  shared edges invalidate shared closures; new modules, aliases, and import-time
  coverage invalidate both graphs' derived data.

Do not cache complete test results by roots alone: tests with the same roots can
execute different imports. Shared-closure caches already retain up to
O(roots × reachable files) references; measure peak memory before adding further
memoization.
