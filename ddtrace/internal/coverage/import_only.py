"""Import-only dependency tracking, without code instrumentation.

Module-body and session imports are shared dependencies. Imports inside a test
stay local to its context, even when other tests cover the same caller file.
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
from ddtrace.internal.coverage.coverage_lines import CoverageLines
from ddtrace.internal.coverage.import_hook_instrumentation import install_import_hook
from ddtrace.internal.coverage.import_hook_instrumentation import uninstall_import_hook
from ddtrace.internal.coverage.mode import CollectorContract
from ddtrace.internal.coverage.mode import CoverageMode
from ddtrace.internal.coverage.mode import ImportName


class ImportOnlyCoverage(CoverageMode):
    is_import_only = True

    def __init__(self, collector: CollectorContract) -> None:
        super().__init__(collector)
        self._import_time_names_by_path: dict[str, set[ImportName]] = defaultdict(set)
        # Shared closures must survive test-local edges, but not new shared edges
        # or module metadata (including aliases and previously empty resolutions).
        self._static_import_time_dependency_paths_cache: dict[str, frozenset[str]] = {}

    def should_track(self, path: str) -> bool:
        return self._collector._is_instrumentable_path(Path(path))

    def install(self) -> None:
        install_import_hook(self, self.should_track)
        self.backfill_already_imported()

    def uninstall(self) -> None:
        uninstall_import_hook()

    def record_import_file(self, path: str) -> None:
        self._collector._record_file_covered(path)
        # First loads nest an import context inside the test context. Attribute
        # imported files to the innermost open TEST context, not the module body.
        stack = ctx_collectors.get()
        if not stack and _PY_GE_314:
            stack = getattr(_tls_coverage, "stack", ())
        for context in reversed(stack):
            if not context.closed and not context.is_import_coverage:
                if path not in context._covered_files:
                    context._covered_files.add(path)
                    context._covered_lines[path].add(0)
                return

    def record_import_edge(self, path: str, import_name: ImportName) -> None:
        stack = ctx_collectors.get()
        if not stack and _PY_GE_314:
            stack = getattr(_tls_coverage, "stack", ())
        for context in reversed(stack):
            if context.closed:
                continue
            self._collector.hook_import(path, import_name)
            if context.is_import_coverage:
                self._add_import_time_name(path, import_name)
            else:
                context._import_names[path].add(import_name)
            return
        # A copied stack containing only completed collectors is expired, not a
        # session scope. Never borrow TLS or publish its late imports as shared.
        if not stack:
            self._collector.hook_import(path, import_name)
            self._add_import_time_name(path, import_name)

    def _add_import_time_name(self, path: str, import_name: ImportName) -> None:
        if import_name not in self._import_time_names_by_path[path]:
            self._import_time_names_by_path[path].add(import_name)
            self._static_import_time_dependency_paths_cache.clear()

    def instrument_module(self, code: CodeType, module: ModuleType) -> CodeType:
        self._collector.lines[code.co_filename].add(0)
        return code

    def on_module_body_finished(self, module: ModuleType) -> None:
        # importlib aliases captured before installation bypass the wrapper, but
        # the watchdog still sees first loads. Make them traversable and covered.
        if module.__file__ is not None:
            module_file = os.path.realpath(module.__file__)
            self._collector._record_import_time_sentinel(module_file)
            self.record_import_file(module_file)

    def backfill_already_imported(self) -> None:
        """Recover traversable nodes and unconditional imports missed at startup."""
        collector = self._collector
        for name, module in list(sys.modules.items()):
            module_file = getattr(module, "__file__", None)
            if not module_file or not module_file.endswith(".py"):
                continue
            module_file = os.path.realpath(module_file)
            if not self.should_track(module_file):
                continue
            if module_file not in collector._import_time_covered:
                collector._record_import_time_sentinel(module_file)
            collector._register_import_time_name(name, module_file)
            for edge in self._static_module_level_import_names(name, module_file):
                self._add_import_time_name(module_file, edge)

    @staticmethod
    def _static_module_level_import_names(module_name: str, module_file: str) -> tuple[ImportName, ...]:
        try:
            # Bytes preserve Python's source-encoding handling.
            tree = ast.parse(Path(module_file).read_bytes(), module_file)
        except Exception:
            return ()

        # Package initializers resolve relative imports from the package itself.
        pkg = module_name if Path(module_file).name == "__init__.py" else module_name.rpartition(".")[0]
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".") if pkg else []
                    base = base[: len(base) - node.level + 1] if node.level <= len(base) else []
                    prefix = ".".join(base + ([node.module] if node.module else []))
                else:
                    prefix = node.module or ""
                if prefix:
                    names.add(prefix)
                names.update(
                    f"{prefix}.{alias.name}" if prefix else alias.name for alias in node.names if alias.name != "*"
                )
        return (("", tuple(sorted(names))),) if names else ()

    def on_import_metadata_changed(self) -> None:
        self._static_import_time_dependency_paths_cache.clear()

    def static_dependency_paths(self, root_path: str) -> frozenset[str]:
        """Cache the closure over shared edges, independent of any test context."""
        cached = self._static_import_time_dependency_paths_cache.get(root_path)
        if cached is not None:
            return cached
        dependencies: set[str] = set()
        visited: set[str] = set()
        pending = {root_path}
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            if path not in self._collector._import_time_covered:
                continue
            dependencies.add(path)
            for edge in self._import_time_names_by_path.get(path, ()):
                pending.update(self._collector._get_dependency_paths_for_import_name(edge) - visited)
        result = frozenset(dependencies)
        self._static_import_time_dependency_paths_cache[root_path] = result
        return result

    def expand_context_lines(
        self, covered_lines: dict[str, CoverageLines], import_names: dict[str, set[ImportName]]
    ) -> None:
        for path in self.context_dependency_paths(covered_lines, import_names):
            covered_lines[path].add(0)

    def expand_context_files(self, paths: set[str], import_names: dict[str, set[ImportName]]) -> t.AbstractSet[str]:
        return paths | self.context_dependency_paths(paths, import_names)

    def context_dependency_paths(
        self, root_paths: t.Iterable[str], import_names: dict[str, set[ImportName]]
    ) -> frozenset[str]:
        """Expand roots together, visiting overlapping shared closures only once."""
        result: set[str] = set()
        pending = list(root_paths)
        while pending:
            path = pending.pop()
            if path in result or path not in self._collector._import_time_covered:
                continue
            static_paths = self.static_dependency_paths(path)
            new_paths = static_paths - result if result else static_paths
            result.update(new_paths)
            # Intersect in C over the smaller set: context-local callers are
            # usually much fewer than the files in the shared closure.
            if import_names:
                for caller in import_names.keys() & new_paths:
                    for edge in import_names[caller]:
                        pending.extend(self._collector._get_dependency_paths_for_import_name(edge))
        return frozenset(result)
