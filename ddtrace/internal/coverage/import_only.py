"""Import-only coverage support: state and logic for `_DD_COVERAGE_IMPORT_ONLY`.

Held in its own module (rather than inside the collector, code.py) so the collector
only carries delegation points: install() creates this helper, wires the import-event
wrapper to it, and the per-context closures call back into it. The collector is
passed in (duck-typed) to avoid a circular import; the shared context stack lives in
context_state.py, which both modules import.

What lives here:

- import-event recording: files seen through import statements (hook_import_file)
  and dependency-edge routing by scope (hook_import): module bodies record into a
  shared import-time edge store; edges executed inside a test window belong to that
  test's context only; edges executed outside any window (session-level lazy
  imports) go back to the shared store.
- module bookkeeping (record_module): the line-0 sentinel for a module whose
  import context carried no line data (no instrumentation ran during the body).
- backfill of modules imported before the collector installed: they never went
  through transform, so without the backfill they are invisible closure nodes and
  edges recorded later (e.g. lazy dialect loads) hang off them unreachable.
- the import-time closures: the cached closure of a root over the shared
  (module-level) edges, and the per-context expansion on top of it.
"""

import ast
from collections import defaultdict
import os
from pathlib import Path
import sys
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.context_state import _PY_GE_314
from ddtrace.internal.coverage.context_state import _tls_coverage
from ddtrace.internal.coverage.context_state import ctx_collectors
from ddtrace.internal.coverage.context_state import ctx_coverage_enabled


ImportName = tuple[str, tuple[str, ...]]

if t.TYPE_CHECKING:
    from ddtrace.internal.coverage.code import ModuleCodeCollector


class ImportOnlyCoverage:
    """Import-only mode helper, owned by ModuleCodeCollector.

    The collector is referenced as `self._collector` (duck-typed) so this module
    does not import code.py.
    """

    def __init__(self, collector: "ModuleCodeCollector") -> None:
        self._collector = collector
        # caller path -> import names, recorded while a module body was executing
        # (module-level import edges, shared by every context covering the caller).
        self._import_time_names_by_path: dict[str, set[ImportName]] = defaultdict(set)
        # Closure of a root over the shared (import-time) edges. Pure function of
        # the import-time graph, so it is cached and shared by every context.
        self._static_import_time_dependency_paths_cache: dict[str, frozenset[str]] = {}
        # Import events re-check the same file paths over and over.
        self._should_track_path_cache: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Path gating
    # ------------------------------------------------------------------

    def should_track(self, path: str) -> bool:
        """Whether an import-event file should be recorded (same paths transform instruments)."""
        cached = self._should_track_path_cache.get(path)
        if cached is not None:
            return cached
        try:
            result = self._collector._is_instrumentable_path(Path(path))
        except Exception:
            result = False
        self._should_track_path_cache[path] = result
        return result

    # ------------------------------------------------------------------
    # Import-event recording (called by the __import__ wrapper)
    # ------------------------------------------------------------------

    def record_import_file(self, path: str) -> None:
        """Record a file seen through an import event.

        Context recording targets the innermost NON-import collector so that an
        import statement executed during a test window attributes to that test's
        context even when the imported module's own import-time context is on the
        stack (first-time imports). Import-time contexts themselves are skipped:
        their data is only used for the importing module's own file.
        """
        self._collector._record_file_covered(path)

        if ctx_coverage_enabled.get():
            stack = ctx_collectors.get()
        elif _PY_GE_314 and bool(getattr(_tls_coverage, "stack", ())):
            stack = getattr(_tls_coverage, "stack", ())
        else:
            stack = ()
        for collector_ctx in reversed(stack):
            if collector_ctx.closed or collector_ctx.is_import_coverage:
                continue
            if path not in collector_ctx._covered_files:
                collector_ctx._covered_files.add(path)
                collector_ctx._covered_lines[path].add(0)
            return

    def record_import_edge(self, path: str, import_name: ImportName) -> None:
        """Record an import dependency edge: globally, then routed by scope.

        The collector's global recording (_import_names_by_path, used by the
        include_imported expansions) happens first; the scoped routing below decides
        which closure view (shared module-level vs per-test vs session-level) also
        gets the edge.

        The innermost open collector decides the scope: an import-time context means
        the edge was executed by a module body (a module-level import, shared by every
        context that covers the module); otherwise the edge belongs to the innermost
        test context, so tests sharing a module do not pollute each other's dependency
        closures.
        """
        self._collector.hook_import(path, import_name)

        if ctx_coverage_enabled.get():
            stack = ctx_collectors.get()
        elif _PY_GE_314 and bool(getattr(_tls_coverage, "stack", ())):
            stack = getattr(_tls_coverage, "stack", ())
        else:
            stack = ()
        for collector_ctx in reversed(stack):
            if collector_ctx.closed:
                continue
            if collector_ctx.is_import_coverage:
                self._add_import_time_name(path, import_name)
            else:
                collector_ctx._import_names[path].add(import_name)
            return
        # No open collector: the import statement executed outside any coverage
        # window (e.g. a session-level lazy import such as engine/dialect loading).
        # Treat it as a module-level edge so every context covering the importing
        # file expands it.
        self._add_import_time_name(path, import_name)

    def _add_import_time_name(self, path: str, import_name: ImportName) -> None:
        if import_name not in self._import_time_names_by_path[path]:
            self._import_time_names_by_path[path].add(import_name)
            self._static_import_time_dependency_paths_cache.clear()

    # ------------------------------------------------------------------
    # Module bookkeeping (called from transform / after_import)
    # ------------------------------------------------------------------

    def record_module(self, module: ModuleType) -> None:
        """Finish import-time bookkeeping for an imported module.

        No instrumentation runs during the module body, so the import context
        carries no line data. Record the line-0 sentinel so the file participates
        in the import-time closure graph, and mark the file covered when a coverage
        window is open (import events may miss the module itself:
        importlib.import_module bypasses the wrapper).
        """
        if module.__file__ is None:
            return
        module_file = os.path.realpath(module.__file__)  # canonicalize: __file__ is unresolved before 3.10
        self._collector._record_import_time_sentinel(module_file)
        self._collector._record_file_covered(module_file)

    def backfill_already_imported(self) -> None:
        """Register modules that were imported before the collector installed.

        They never went through transform, so without this they have no line-0
        sentinel and no name-to-path entry: they are invisible to the import-time
        closure, and edges recorded later through them (e.g. a lazy dialect load
        during collection) hang off an unreachable node. Importability of these
        modules is a session-level dependency (raising in any of them breaks the
        whole session), so making them traversable is also the semantically right
        call.

        Their module-level import statements also executed before the wrapper was
        installed, so those edges are recovered statically (AST, top-level statements
        only — a module-level import is a hard dependency of the importing module).
        """
        collector = self._collector
        for name, module in list(sys.modules.items()):
            module_file = getattr(module, "__file__", None)
            if not module_file or not module_file.endswith(".py"):
                continue
            module_file = os.path.realpath(module_file)  # canonicalize: __file__ is unresolved before 3.10
            if not self.should_track(module_file):
                continue
            if module_file not in collector._import_time_covered:
                collector._record_import_time_sentinel(module_file)
            collector._register_import_time_name(name, module_file)
            for edge in self._static_module_level_import_names(name, module_file):
                self._add_import_time_name(module_file, edge)

    @staticmethod
    def _static_module_level_import_names(module_name: str, module_file: str) -> tuple[ImportName, ...]:
        """The module-level import statements of an already-imported module, as one
        ("", absolute names) edge tuple suitable for the shared resolution logic.
        """
        try:
            source = Path(module_file).read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, module_file)
        except Exception:
            return ()

        pkg = module_name.rpartition(".")[0]
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".") if pkg else []
                    if node.level > 1:
                        cut = node.level - 1
                        base = base[: len(base) - cut] if cut < len(base) else []
                    abs_prefix = ".".join(base + ([node.module] if node.module else []))
                else:
                    abs_prefix = node.module or ""
                if abs_prefix:
                    names.add(abs_prefix)
                for alias in node.names:
                    if alias.name != "*":
                        names.add(f"{abs_prefix}.{alias.name}" if abs_prefix else alias.name)
        if not names:
            return ()
        return (("", tuple(sorted(names))),)

    # ------------------------------------------------------------------
    # Closures
    # ------------------------------------------------------------------

    def on_import_metadata_changed(self) -> None:
        """Collector notification: import-time metadata changed, drop derived caches."""
        self._static_import_time_dependency_paths_cache.clear()

    def install(self) -> None:
        from ddtrace.internal.coverage.import_hook_instrumentation import install_import_hook

        install_import_hook(self, self.should_track)
        self.backfill_already_imported()

    def uninstall(self) -> None:
        from ddtrace.internal.coverage.import_hook_instrumentation import uninstall_import_hook

        uninstall_import_hook()

    def instrument_module(self, code: CodeType, module: ModuleType) -> CodeType:
        self._collector.lines[code.co_filename].add(0)
        return code

    def after_module_context(self, code: CodeType, module: ModuleType) -> None:
        """No empty-module recording needed without instrumentation."""

    def on_module_body_finished(self, module: ModuleType) -> None:
        self.record_module(module)

    def arm(self, import_context: bool) -> None:
        """Nothing to re-arm: no monitoring events."""

    @property
    def is_import_only(self) -> bool:
        return True

    def _direct_dependency_paths(self, path: str, import_names: dict[str, set[ImportName]]) -> frozenset[str]:
        dependency_paths: set[str] = set()
        for dependencies in import_names.get(path, ()):
            dependency_paths.update(self._collector._get_dependency_paths_for_import_name(dependencies))
        return frozenset(dependency_paths)

    def static_dependency_paths(self, root_path: str) -> frozenset[str]:
        """Closure of a root over the module-level (import-time) edges only. Cached.

        These edges are shared by every context, so the closure is a pure function
        of the import-time graph: per-context closures expand on top of it with the
        context's own (few) edges instead of re-walking the shared graph per test.
        """
        cached = self._static_import_time_dependency_paths_cache.get(root_path)
        if cached is not None:
            return cached

        dependency_paths = set()
        visited_paths = set()
        to_visit_paths = {root_path}

        while to_visit_paths:
            path = to_visit_paths.pop()

            if path in visited_paths:
                continue

            visited_paths.add(path)

            if path not in self._collector._import_time_covered:
                continue

            dependency_paths.add(path)
            for dependencies in self._import_time_names_by_path.get(path, ()):
                to_visit_paths.update(
                    self._collector._get_dependency_paths_for_import_name(dependencies) - visited_paths
                )

        result = frozenset(dependency_paths)
        self._static_import_time_dependency_paths_cache[root_path] = result
        return result

    def context_dependency_paths(self, root_path: str, import_names: dict[str, set[ImportName]]) -> frozenset[str]:
        """Closure of a root over the shared edges plus one context's own edges.

        Equivalent to a BFS over the merged edge maps, but expands only the (few)
        paths with edges recorded in this context: everything else comes from the
        cached static closure.
        """
        if root_path not in self._collector._import_time_covered:
            return frozenset()

        result = set(self.static_dependency_paths(root_path))
        to_visit = [path for path in result if path in import_names]
        while to_visit:
            path = to_visit.pop()
            for dep in self._direct_dependency_paths(path, import_names):
                if dep in result or dep not in self._collector._import_time_covered:
                    continue
                result.add(dep)
                if dep in import_names:
                    to_visit.append(dep)
                for static_dep in self.static_dependency_paths(dep):
                    if static_dep not in result:
                        result.add(static_dep)
                        if static_dep in import_names:
                            to_visit.append(static_dep)
        return frozenset(result)

    def expand_context_lines(self, covered_lines: dict[str, t.Any], import_names: dict[str, set[ImportName]]) -> None:
        """File-level variant of the collector's _add_import_time_lines for one context."""
        processed_paths = set()
        for root_path in tuple(covered_lines.keys()):
            for dependency_path in self.context_dependency_paths(root_path, import_names):
                if dependency_path not in processed_paths:
                    processed_paths.add(dependency_path)
                    covered_lines[dependency_path].add(0)

    def expand_context_files(
        self, covered_file_paths: set[str], import_names: dict[str, set[ImportName]]
    ) -> t.AbstractSet[str]:
        """File-level covered paths with import-time dependencies added, for one context."""
        paths = set(covered_file_paths)
        import_time_covered = self._collector._import_time_covered
        for root_path in covered_file_paths:
            if root_path in import_time_covered:
                paths.update(self.context_dependency_paths(root_path, import_names))
        return paths
