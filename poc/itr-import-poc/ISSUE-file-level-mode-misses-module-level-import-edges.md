# Issue: file-level coverage (instrumented mode) misses module-level import edges, under-attributing real dependencies

**Discovered by:** the import-only coverage POC (`_DD_COVERAGE_IMPORT_ONLY`, branch
`gnufede/itr-import-poc`) while comparing citestcov payloads between the instrumented
file-level mode ("off") and the import-only mode ("on") on a real corpus.

## Summary

The instrumented file-level mode (PY_START-based, default on 3.12+) does not record
import dependency edges for **module-level import statements**. Every module's body
executes exactly once, at import time, inside an import-time context whose events are
not armed; import names are only recorded from code objects that *execute during a
test* (PY_START + static import analysis of that code object). As a result the
dependency graph is missing module-body edges, and the import-time closure expansion
stops one level short of files that are only reachable through a module-level import.

This is a **false-skip risk in production ITR**: a module-level import is a hard
dependency of the importing module, so a change to a file that is only reachable via
such an edge does not mark the affected tests as impacted.

## Evidence (sqlalchemy corpus, 3850 tests, ddtest plugin, citestcov payloads)

Files attributed by import-only to ~every test but by instrumented file-level mode to
~no tests, each with the module-level import that produces the edge:

| dependency | importing statement (executed once, at import) | on / off test attribution |
|---|---|---|
| `pool/events.py` | `pool/__init__.py:20: from . import events` | 3850 / 0 |
| `engine/events.py` | `engine/__init__.py:18: from . import events as events` | 3850 / 12 |
| `orm/evaluator.py` | `orm/bulk_persistence.py:30: from . import evaluator` | 3850 / 30 |
| `sql/crud.py` | `sql/__init__.py` module-level | 3850 / 57 |
| `future/__init__.py`, `future/engine.py` | `sqlalchemy/__init__` preloaded chain | 3850 / 0 |
| `orm/dynamic.py`, `orm/persistence.py`, `orm/clsregistry.py`, `connectors/asyncio.py` | analogous | 3850 / 2–86 |

In total: ~33 files, ~177k (test,file) attribution pairs present in import-only
payloads and absent from instrumented payloads. "Raising" anywhere in these files
(e.g. appending `raise Exception` to `pool/events.py`) breaks the sqlalchemy import
entirely, i.e. every test depends on them — the instrumented mode's attribution is
the under-report.

## How import-only finds these edges

The `builtins.__import__` wrapper (import_hook_instrumentation.py) fires for every
executed import statement — including cache hits, and including imports that execute
during module bodies at import time:

1. When module `pool/__init__.py` is imported (e.g. at collection), its body executes
   `from . import events`; the wrapper records the edge `pool/__init__.py ->
   sqlalchemy.pool.events`.
2. The edge is routed by the innermost open coverage collector: import-time contexts
   (module bodies) record into a shared "import-time edge store"
   (`_import_time_names_by_path`), shared by every context that later covers the
   importing module.
3. Per test, the pytest integration anchors the test's own file as a covered root;
   the anchored closure expansion follows import-time edges transitively, so every
   test whose closure reaches `pool/__init__.py` also reports `pool/events.py`.

## Why the instrumented mode misses them

- Module bodies execute inside `CollectInContext(is_import_coverage=True)` scopes;
   the engines skip re-arming events for import contexts, so PY_START never fires
   for module-level code objects.
- Import names are recorded when a code object *starts during a test* (static import
   analysis of that code object, see `_extract_lines_and_imports` /
   `_event_handler` in instrumentation_py3_12.py). A module body never starts during
   a test, so its statically-known import names never enter
   `_import_names_by_path`.
- The accurate import-hook injection (`_DD_COVERAGE_ACCURATE_IMPORTS`) fires only
   when an import statement executes — module bodies don't execute during tests —
   so it does not record these edges either.

Empirical confirmation: in an instrumented-mode process, the edge store for e.g.
`sql/events.py` contains only function-body edges (from lazily-executed machinery),
while the import-only store for the same file contains the actual module-body imports
(`from sqlalchemy import event`, `typing`, `sql.base`, ...).

## Reproduction

```bash
# corpus: any repo with module-level import trees (e.g. sqlalchemy)
# 1. run with instrumented file-level coverage and capture citestcov payloads
#    (fake intake + ddtest plugin, see poc/itr-import-poc/corpus/)
# 2. run with _DD_COVERAGE_IMPORT_ONLY=1, same capture
# 3. diff per-test file sets: pool/events.py, engine/events.py, orm/evaluator.py, ...
#    are in every import-only set and in almost no instrumented set
```

## Suggested fix (instrumented mode)

The instrumentation already computes each module's statically-known import names at
transform time (`_extract_lines_and_imports(code, package, collect_imports=True)`).
Recording those names into `_import_names_by_path[co_filename]` at transform —
instead of only when a code object starts during a test — would give the instrumented
mode the same module-level edges, with no extra runtime cost (the analysis already
runs for instrumentation). Caveat: static names are conservative (they include
runtime-false guarded imports — the semantics `_DD_COVERAGE_ACCURATE_IMPORTS` exists
to refine), so this increases over-attribution to the same level import-only's
import-time edge store has; the POC's per-context edge isolation (edges recorded
inside a test window belong to that test's context) is the more precise variant.

## Related (separate) finding

The reverse difference (instrumented mode reporting MORE than import-only) is the
known import-only blind spot: files whose code executes during tests without any
import statement executing (conftest/harness files loaded pre-install via
importlib, lazy `__getattr__` machinery) — plus module-level dependencies reachable
only through modules imported before the collector is installed (no transform
sentinel, so the closure cannot traverse them). Fix directions: install-time
backfill of already-imported workspace modules into `_import_time_covered` +
`_import_time_name_to_path`, anchoring conftest/plugin files in the pytest
integration, or installing the `__import__` wrapper before pytest loads conftest
plugins.
