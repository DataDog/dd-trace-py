"""Import-only coverage mode: import-event tracking without code instrumentation.

Wraps `builtins.__import__` and `importlib.import_module` (including cached
imports) to record caller files, imported files, and dependency edges — no code
object is instrumented. Module-body and session imports are shared dependencies;
imports inside a test stay local to its context. Install-time backfill recovers
pre-imported modules and their unconditional top-level import statements.
"""

import ast
import builtins
from collections import defaultdict
from functools import lru_cache
import importlib
from importlib.util import resolve_name
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
from ddtrace.internal.coverage.mode import CollectorContract
from ddtrace.internal.coverage.mode import CoverageMode
from ddtrace.internal.coverage.mode import ImportName
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _conservative_import_only_enabled() -> bool:
    """Whether workspace modules are attributed to every test as closure roots."""
    from ddtrace.internal.settings import env

    return env.get("_DD_COVERAGE_IMPORT_ONLY_CONSERVATIVE", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Import-event wrappers
# ---------------------------------------------------------------------------


class _TrackedPaths(dict[str, t.Optional[str]]):
    """One lookup returns a canonical included path, or None for an exclusion.

    On Python < 3.10 module __file__ is not symlink-resolved. Canonicalization
    keeps events, anchors and backfill in the same key space (e.g. /var versus
    /private/var on macOS). Include/exclude paths are fixed for each installation.
    """

    def __init__(self, should_track: t.Callable[[str], bool]) -> None:
        super().__init__()
        self.should_track = should_track

    def __missing__(self, path: str) -> t.Optional[str]:
        resolved = os.path.realpath(path)
        tracked = resolved if self.should_track(resolved) else None
        self[path] = tracked
        return tracked


def _resolve_absolute_name(name: str, glb: t.Optional[t.Mapping[str, t.Any]], level: int) -> str:
    """Resolve a possibly-relative import name to its absolute module name."""
    if not level:
        return name
    if glb is None:
        return name
    pkg = glb.get("__package__")
    if not pkg:
        return name
    return resolve_name("." * level + name, pkg)


@lru_cache(maxsize=4096)
def _module_name_prefixes(abs_name: str) -> tuple[str, ...]:
    """Cache only string work; module objects and their paths must be read live."""
    parts = abs_name.split(".")
    return tuple(".".join(parts[:i]) for i in range(1, len(parts) + 1))


def _record(
    recorder: t.Any,
    tracked_paths: _TrackedPaths,
    abs_name: str,
    fromlist: t.Any,
    glb: t.Optional[t.Mapping[str, t.Any]],
) -> None:
    caller = glb.get("__file__") if glb is not None else None
    if not caller:
        try:
            caller = sys._getframe(2).f_code.co_filename
        except Exception:
            return
    caller = tracked_paths[caller]

    # A simple import has exactly one candidate. Excluded callers can finish
    # here without prefix expansion or caller/edge bookkeeping, but a framework
    # importing a workspace module must still record that target.
    if caller is None and not fromlist and "." not in abs_name:
        file = getattr(sys.modules.get(abs_name), "__file__", None)
        if file and (tracked := tracked_paths[file]) is not None:
            recorder.record_import_file(tracked)
        return

    for module_name in _module_name_prefixes(abs_name):
        module = sys.modules.get(module_name)
        file = getattr(module, "__file__", None)
        if file and (tracked := tracked_paths[file]) is not None:
            recorder.record_import_file(tracked)
    if fromlist:
        for sub in fromlist:
            if isinstance(sub, str) and sub:
                module = sys.modules.get(f"{abs_name}.{sub}")
                file = getattr(module, "__file__", None)
                if file and (tracked := tracked_paths[file]) is not None:
                    recorder.record_import_file(tracked)

    if caller is None:
        return

    recorder.record_import_file(caller)
    names = [abs_name]
    if fromlist:
        names.extend(f"{abs_name}.{sub}" for sub in fromlist if isinstance(sub, str) and sub)
    recorder.record_import_edge(caller, ("", tuple(names)))


class _ImportHook:
    """One installation, retaining its own delegates after it is deactivated.

    Other tools can retain our wrappers. Clearing shared delegate globals breaks
    those callers on uninstall; replacing them on reinstall creates a cycle.
    """

    def __init__(self, recorder: t.Any, should_track: t.Callable[[str], bool]) -> None:
        self.recorder: t.Optional[t.Any] = recorder
        self.tracked_paths = _TrackedPaths(should_track)
        self.original_import = builtins.__import__
        self.original_import_module = importlib.import_module

    def import_name(
        self,
        name: str,
        globals: t.Optional[t.Mapping[str, t.Any]] = None,  # noqa: A002
        locals: t.Optional[t.Mapping[str, t.Any]] = None,  # noqa: A002
        fromlist: t.Any = (),
        level: int = 0,
    ) -> t.Any:
        module = self.original_import(name, globals, locals, fromlist, level)
        recorder = self.recorder
        if recorder is not None:
            try:
                abs_name = _resolve_absolute_name(name, globals, level)
                if abs_name:
                    _record(recorder, self.tracked_paths, abs_name, fromlist, globals)
            except Exception:
                log.debug("Import hook recording failed for %r", name, exc_info=True)
        return module

    def import_module(self, name: str, package: t.Optional[str] = None) -> t.Any:
        module = self.original_import_module(name, package)
        recorder = self.recorder
        if recorder is not None:
            try:
                abs_name = resolve_name(name, package)
                if abs_name:
                    _record(recorder, self.tracked_paths, abs_name, (), sys._getframe(1).f_globals)
            except Exception:
                log.debug("importlib.import_module hook recording failed for %r", name, exc_info=True)
        return module


_installed_hook: t.Optional[_ImportHook] = None


def _install_import_hook(recorder: t.Any, should_track: t.Callable[[str], bool]) -> None:
    """Wrap import statements and importlib.import_module, including cache hits."""
    global _installed_hook
    if _installed_hook is not None:
        return
    hook = _installed_hook = _ImportHook(recorder, should_track)
    builtins.__import__ = hook.import_name
    importlib.import_module = hook.import_module


def _uninstall_import_hook() -> None:
    """Deactivate retained wrappers and restore hooks we still own."""
    global _installed_hook
    hook = _installed_hook
    if hook is None:
        return
    hook.recorder = None
    hook.tracked_paths.should_track = lambda path: False
    hook.tracked_paths.clear()
    if builtins.__import__ == hook.import_name:
        builtins.__import__ = hook.original_import
    if importlib.import_module == hook.import_module:
        importlib.import_module = hook.original_import_module
    _installed_hook = None
    _module_name_prefixes.cache_clear()


# Public names kept for the install/uninstall contract and tests.
install_import_hook = _install_import_hook
uninstall_import_hook = _uninstall_import_hook


# ---------------------------------------------------------------------------
# ImportOnlyCoverage mode
# ---------------------------------------------------------------------------


class ImportOnlyCoverage(CoverageMode):
    is_import_only = True

    def __init__(self, collector: CollectorContract) -> None:
        super().__init__(collector)
        self._import_time_names_by_path: dict[str, set[ImportName]] = defaultdict(set)
        # Shared closures must survive test-local edges, but not new shared edges
        # or module metadata (including aliases and previously empty resolutions).
        self._static_import_time_dependency_paths_cache: dict[str, frozenset[str]] = {}
        self._test_file_anchors: dict[Path, tuple[str, ...]] = {}
        # Workspace modules attributed to every test when the conservative flag
        # is enabled; see expand_context_files for the rationale.
        self._workspace_root_paths: set[str] = set()
        self._conservative = _conservative_import_only_enabled()

    def should_track(self, path: str) -> bool:
        return self._collector._is_instrumentable_path(Path(path))

    def install(self) -> None:
        _install_import_hook(self, self.should_track)
        self.backfill_already_imported()

    def uninstall(self) -> None:
        _uninstall_import_hook()

    def anchor_test_file(self, test_file: Path, anchor: t.Callable[[str], None]) -> None:
        """Discover anchors once per test file and installation, not once per test."""
        try:
            # pytest < 7 passes py.path.local; mocked/abstract items may have no file.
            test_file = Path(test_file)
            paths = self._test_file_anchors.get(test_file)
            if paths is None:
                if not test_file.is_file():
                    return
                resolved = test_file.resolve()
                files = [str(resolved)]
                for parent in resolved.parents:
                    init = parent / "__init__.py"
                    if not init.is_file():
                        break
                    files.append(str(init))
                # conftests apply even across non-package directories.
                files.extend(str(p / "conftest.py") for p in resolved.parents if (p / "conftest.py").is_file())
                paths = self._test_file_anchors[test_file] = tuple(files)
        except (TypeError, ValueError, OSError):
            return
        for path in paths:
            anchor(path)

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
            if self._conservative and self.should_track(module_file):
                self._workspace_root_paths.add(module_file)

    def backfill_already_imported(self) -> None:
        """Recover traversable nodes, imports (including conditional), and workspace roots missed at startup."""
        collector = self._collector
        workspace_roots: set[str] = set()
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
            workspace_roots.add(module_file)
            for edge in self._static_import_names(name, module_file):
                self._add_import_time_name(module_file, edge)
        if self._conservative:
            # Every pre-imported workspace module is a closure root for every test.
            # More conservative than PY_START: see README for the full trade-off.
            self._workspace_root_paths = workspace_roots

    @staticmethod
    def _static_import_names(module_name: str, module_file: str) -> tuple[ImportName, ...]:
        """All import statements in a module, verified against sys.modules.

        Walks the entire AST (not just top-level body) so imports in `if`, `try`,
        loops, class bodies, and functions are included. Each discovered name is
        checked against sys.modules: only modules that actually loaded (the branch
        or function call ran) are recorded, so dead branches don't create edges.
        A name not in sys.modules means the import never executed — the dependency
        is hypothetical and correctly excluded.
        """
        try:
            # Bytes preserve Python's source-encoding handling.
            tree = ast.parse(Path(module_file).read_bytes(), module_file)
        except Exception:
            return ()

        # Package initializers resolve relative imports from the package itself.
        pkg = module_name if Path(module_file).name == "__init__.py" else module_name.rpartition(".")[0]
        names: set[str] = set()
        # Walk the full tree: conditional imports, try/except imports, and function
        # bodies that were called during the module's own load are all candidates.
        # The sys.modules check below filters out any that never actually ran.
        for node in ast.walk(tree):
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
        # Only names whose module actually loaded are real dependencies.
        # If an import is in a dead branch or an uncalled function, sys.modules
        # won't have the target, so it's excluded — no over-attribution.
        confirmed = {name for name in names if name in sys.modules}
        return (("", tuple(sorted(confirmed))),) if confirmed else ()

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
        expanded = self.context_dependency_paths(paths, import_names)
        if self._workspace_root_paths:
            # Conservative mode: workspace modules (pre-imported and dynamically
            # loaded) are additional closure roots, covering callback/registry
            # patterns where a workspace function is called without an import in
            # the test window. More conservative than PY_START: every workspace
            # module and its transitive deps are attributed to every test, which
            # reduces skippability but eliminates the stored-reference blind spot.
            # Static closures per root are cached, so expansion is O(closure size).
            for root in self._workspace_root_paths:
                expanded |= self.static_dependency_paths(root)
        return paths | expanded

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
