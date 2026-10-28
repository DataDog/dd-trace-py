from collections import OrderedDict
from collections import defaultdict
from copy import deepcopy
from inspect import getmodule
import os
from pathlib import Path
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.context_state import _PY_GE_314
from ddtrace.internal.coverage.context_state import _get_active_collector
from ddtrace.internal.coverage.context_state import _get_ctx_collector
from ddtrace.internal.coverage.context_state import _get_ctx_covered_files
from ddtrace.internal.coverage.context_state import _get_ctx_covered_lines
from ddtrace.internal.coverage.context_state import _tls_coverage
from ddtrace.internal.coverage.context_state import ctx_collectors
from ddtrace.internal.coverage.context_state import ctx_coverage_enabled
from ddtrace.internal.coverage.context_state import ctx_is_import_coverage  # noqa: F401
from ddtrace.internal.coverage.coverage_lines import CoverageLines
from ddtrace.internal.coverage.engine import CoverageEngine
from ddtrace.internal.coverage.engine import create_engine
from ddtrace.internal.coverage.import_hook_instrumentation import install_import_hook
from ddtrace.internal.coverage.import_hook_instrumentation import uninstall_import_hook
from ddtrace.internal.coverage.import_only import ImportOnlyCoverage
from ddtrace.internal.coverage.report import gen_json_report
from ddtrace.internal.coverage.report import print_coverage_report
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.coverage.util import is_file_level_coverage_enabled
from ddtrace.internal.coverage.util import is_import_only_coverage_enabled
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.packages import platlib_path
from ddtrace.internal.packages import platstdlib_path
from ddtrace.internal.packages import purelib_path
from ddtrace.internal.packages import stdlib_path
from ddtrace.internal.utils.inspection import resolved_code_origin
from ddtrace.internal.utils.obfuscation import is_obfuscated_code
from ddtrace.internal.utils.paths import is_contained
from ddtrace.internal.utils.paths import relative_path


log = get_logger(__name__)

_original_exec = exec

_FILE_LEVEL_COVERED_PATHS_CACHE_MAX_SIZE = 4096
_SITE_PACKAGES_DIRNAMES = frozenset(("site-packages", "dist-packages"))


def _is_site_packages_path(path: Path) -> bool:
    return not _SITE_PACKAGES_DIRNAMES.isdisjoint(path.parts)


class ModuleCodeCollector(ModuleWatchdog):
    _instance: t.Optional["ModuleCodeCollector"] = None

    def __init__(self) -> None:
        super().__init__()
        # Coverage collection configuration
        self._collect_import_coverage: bool = False
        self._include_paths: list[Path] = []
        # By default, exclude standard / venv paths (eg: avoids over-instrumenting cases where a virtualenv is created
        # in the root directory of a repository)
        self._exclude_paths: list[Path] = [stdlib_path, platstdlib_path, platlib_path, purelib_path]

        # Avoid instrumenting anything in the current module
        self._exclude_paths.append(Path(__file__).resolve().parent)

        self._coverage_enabled: bool = False
        self._file_level_coverage: bool = False
        # Import-only mode: coverage is collected exclusively from import events (see
        # import_hook_instrumentation.py); no code object is instrumented.
        self._import_only_coverage: bool = False
        # Version-specific instrumentation (sys.monitoring vs bytecode traps, probes,
        # event re-arming, empty-module handling) is encapsulated behind the engine API —
        # see ddtrace.internal.coverage.engine.
        self._engine: CoverageEngine = create_engine(self)
        self.seen: set[tuple[CodeType, str]] = set()

        # Data structures for coverage data
        self.lines: defaultdict[str, CoverageLines] = defaultdict(CoverageLines)
        self.covered: defaultdict[str, CoverageLines] = defaultdict(CoverageLines)
        self._covered_files: set[str] = set()

        # Import-time coverage data
        self._import_time_covered: defaultdict[str, CoverageLines] = defaultdict(CoverageLines)
        self._import_time_contexts: dict[str, ModuleCodeCollector.CollectInContext] = {}
        self._import_time_name_to_path: dict[str, str] = {}
        self._import_names_by_path: dict[str, set[tuple[str, tuple[str, ...]]]] = defaultdict(set)
        # Import metadata can grow during late/dynamic imports. Clear this cache whenever import coverage
        # metadata changes so per-test import-time augmentation does not miss newly discovered dependencies.
        self._direct_import_time_dependency_paths_cache: dict[str, frozenset[str]] = {}
        self._import_time_dependency_paths_cache: dict[str, frozenset[str]] = {}
        # Shared name-resolution cache (one (package, names) tuple to file paths; depends
        # only on _import_time_name_to_path), used by both modes' closures.
        self._dependency_paths_for_import_name_cache: dict[tuple[str, tuple[str, ...]], frozenset[str]] = {}
        self._file_level_covered_paths_cache: OrderedDict[frozenset[str], frozenset[str]] = OrderedDict()
        # Import-only mode helper (see import_only.py); None in instrumented modes.
        self._import_only: t.Optional[ImportOnlyCoverage] = None

        # Replace the built-in exec function with our own in the pytest globals
        try:
            import _pytest.assertion.rewrite as par

            par.exec = self._exec
        except ImportError:
            pass

    @classmethod
    def install(
        cls,
        include_paths: t.Optional[list[Path]] = None,
        collect_import_time_coverage: bool = False,
        file_level_coverage: t.Optional[bool] = None,
    ):
        if ModuleCodeCollector.is_installed():
            return

        super().install()

        if cls._instance is None:
            # installation failed
            return

        if include_paths is None:
            include_paths = [Path(os.getcwd())]

        cls._instance._include_paths = include_paths
        cls._instance._collect_import_coverage = collect_import_time_coverage
        cls._instance._file_level_coverage = (
            is_file_level_coverage_enabled() if file_level_coverage is None else file_level_coverage
        )

        if is_import_only_coverage_enabled():
            # Import-only mode is file-level by construction (paths only, no lines), and the
            # import-time dependency graph is the attribution mechanism for module-level
            # imports, so both are forced on. The __import__ wrapper replaces all code
            # instrumentation.
            cls._instance._import_only_coverage = True
            cls._instance._file_level_coverage = True
            cls._instance._collect_import_coverage = True
            import_only = ImportOnlyCoverage(cls._instance)
            cls._instance._import_only = import_only
            install_import_hook(import_only, import_only.should_track)
            import_only.backfill_already_imported()

        if cls._instance._collect_import_coverage:
            ModuleCodeCollector.register_import_exception_hook(
                lambda x: True, cls._instance._exit_context_on_exception_hook
            )

    def hook_import(self, path: str, import_name: tuple[str, tuple[str, ...]]) -> None:
        if not self._collect_import_coverage:
            return
        if import_name not in self._import_names_by_path[path]:
            self._import_names_by_path[path].add(import_name)
            self._direct_import_time_dependency_paths_cache.pop(path, None)
            self._import_time_dependency_paths_cache.clear()
            self._file_level_covered_paths_cache.clear()

    def hook_file(self, path: str) -> None:
        if self._coverage_enabled and path not in self._covered_files:
            self._covered_files.add(path)
            self.covered[path].add(0)

        if ctx_coverage_enabled.get():
            collector = _get_ctx_collector(True)
        elif _PY_GE_314 and bool(getattr(_tls_coverage, "stack", ())):
            collector = _get_ctx_collector(False)
        else:
            return
        if collector is not None:
            ctx_covered_file_paths = collector._covered_files
            if path not in ctx_covered_file_paths:
                ctx_covered_file_paths.add(path)
                collector._covered_lines[path].add(0)

    def hook_line(self, path: str, line: int) -> None:
        if self._coverage_enabled:
            lines = self.covered[path]
            lines.add(line)

        if ctx_coverage_enabled.get():
            collector = _get_ctx_collector(True)
        elif _PY_GE_314 and bool(getattr(_tls_coverage, "stack", ())):
            collector = _get_ctx_collector(False)
        else:
            return
        if collector is not None:
            # Import-time contexts store their lines in a non-context variable to be aggregated on request when
            # reporting coverage
            ctx_lines = collector._covered_lines[path]
            ctx_lines.add(line)

    def arm(self, import_context: bool = False) -> None:
        """Re-arm instrumentation events for a new coverage context.

        The engine decides what this means per Python version: sys.monitoring
        `restart_events()` on 3.12+, bytecode-probe flag resets on 3.10/3.11, a no-op on
        3.9. `import_context` marks per-module-import contexts, which engines with
        self-disabling events may skip re-arming for. Import-only mode arms nothing.
        """
        if self._import_only_coverage:
            return
        self._engine.arm(import_context=import_context)

    def hook(self, arg: tuple[int, str, t.Optional[tuple[str, tuple[str, ...]]]]):
        line: int
        path: str
        import_name: t.Optional[tuple[str, tuple[str, ...]]]
        line, path, import_name = arg

        if self._file_level_coverage and line == 0:
            self.hook_file(path)
        else:
            self.hook_line(path, line)

        if import_name is not None:
            self.hook_import(path, import_name)

    @classmethod
    def inject_coverage(
        cls,
        lines: t.Optional[dict[str, CoverageLines]] = None,
        covered: t.Optional[dict[str, CoverageLines]] = None,
    ):
        """Inject coverage data into the collector. This can be used to arbitrarily add covered files."""
        instance = cls._instance

        if instance is None:
            return

        ctx_covered_lines = None
        if ctx_coverage_enabled.get():
            ctx_covered_lines = _get_ctx_covered_lines()

        if lines:
            for path, path_lines in lines.items():
                instance.lines[path].update(path_lines)
        if covered:
            ctx_covered_file_paths = _get_ctx_covered_files() if ctx_coverage_enabled.get() else None
            for path, path_covered in covered.items():
                if instance._coverage_enabled:
                    instance.covered[path].update(path_covered)
                    if instance._file_level_coverage:
                        instance._covered_files.add(path)
                if ctx_coverage_enabled.get() and ctx_covered_lines is not None:
                    ctx_covered_lines[path].update(path_covered)
                    if instance._file_level_coverage and ctx_covered_file_paths is not None:
                        ctx_covered_file_paths.add(path)

    @classmethod
    def report(cls, workspace_path: Path, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        executable_lines = instance.lines
        covered_lines = instance._get_covered_lines()

        print_coverage_report(executable_lines, covered_lines, workspace_path, ignore_nocover=ignore_nocover)

    @classmethod
    def write_json_report_to_file(cls, filename: str, workspace_path: Path, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        executable_lines = instance.lines
        covered_lines = instance._get_covered_lines()

        with open(filename, "w") as f:
            f.write(gen_json_report(executable_lines, covered_lines, workspace_path, ignore_nocover=ignore_nocover))

    def _get_covered_lines(self, include_imported: bool = False) -> dict[str, CoverageLines]:
        # Covered lines should always be a copy to make sure the original cannot be altered
        covered_lines = deepcopy(_get_ctx_covered_lines() if ctx_coverage_enabled.get() else self.covered)
        if include_imported:
            self._add_import_time_lines(covered_lines)

        return covered_lines

    def _get_dependency_paths_for_import_name(self, import_name: tuple[str, tuple[str, ...]]) -> frozenset[str]:
        """Resolve one (package, names) dependency tuple to file paths, cached.

        The resolution depends only on _import_time_name_to_path, so it can be shared by
        every edge map (global, import-time, per-context) and is invalidated together
        with the other import-time caches when a new module is registered.
        """
        cached = self._dependency_paths_for_import_name_cache.get(import_name)
        if cached is not None:
            return cached

        dependency_paths = set()
        package, modules = import_name
        for module in modules:
            dep_fqdn = f"{package}.{module}" if package else module
            dep_name = dep_fqdn if dep_fqdn in self._import_time_name_to_path else module
            if dep_name in self._import_time_name_to_path:
                dependency_paths.add(self._import_time_name_to_path[dep_name])

            # Since modules can import from packages below them in the hierarchy, we may also need to find
            # packages that were imported (eg: identifying __init__.py files). We do this by working our way
            # from the module name to the package name "one dot at a time"
            parent_package = dep_fqdn.split(".")[:-1]
            while parent_package:
                parent_package_str = ".".join(parent_package)
                if parent_package_str in self._import_time_name_to_path:
                    dependency_paths.add(self._import_time_name_to_path[parent_package_str])
                if parent_package_str == package:
                    break
                parent_package = parent_package[:-1]

        resolved = frozenset(dependency_paths)
        self._dependency_paths_for_import_name_cache[import_name] = resolved
        return resolved

    def _get_direct_import_time_dependency_paths(self, path: str) -> frozenset[str]:
        cached_dependency_paths = self._direct_import_time_dependency_paths_cache.get(path)
        if cached_dependency_paths is not None:
            return cached_dependency_paths

        dependency_paths: set[str] = set()
        for dependencies in self._import_names_by_path.get(path, set()):
            dependency_paths.update(self._get_dependency_paths_for_import_name(dependencies))

        cached_dependency_paths = frozenset(dependency_paths)
        self._direct_import_time_dependency_paths_cache[path] = cached_dependency_paths
        return cached_dependency_paths

    def _get_import_time_dependency_paths(self, root_path: str) -> frozenset[str]:
        cached_dependency_paths = self._import_time_dependency_paths_cache.get(root_path)
        if cached_dependency_paths is not None:
            return cached_dependency_paths

        dependency_paths = set()
        visited_paths = set()
        to_visit_paths = {root_path}

        while to_visit_paths:
            path = to_visit_paths.pop()

            if path in visited_paths:
                continue

            visited_paths.add(path)

            if path not in self._import_time_covered:
                continue

            dependency_paths.add(path)
            to_visit_paths.update(self._get_direct_import_time_dependency_paths(path) - visited_paths)

        cached_dependency_paths = frozenset(dependency_paths)
        self._import_time_dependency_paths_cache[root_path] = cached_dependency_paths
        return cached_dependency_paths

    def _add_import_time_lines(self, covered_lines):
        """Modify given covered_lines in place and add lines that were covered at import time"""
        processed_paths = set()
        if self._file_level_coverage:
            for root_path in tuple(covered_lines.keys()):
                for dependency_path in self._get_import_time_dependency_paths(root_path):
                    if dependency_path not in processed_paths:
                        processed_paths.add(dependency_path)
                        covered_lines[dependency_path].add(0)
            return

        for root_path in tuple(covered_lines.keys()):
            for path in self._get_import_time_dependency_paths(root_path):
                if path not in processed_paths:
                    processed_paths.add(path)
                    imported_module_lines = self._import_time_covered[path]
                    covered_lines[path].update(imported_module_lines)

    def _get_covered_file_paths_with_imports(self, covered_file_paths: set[str]) -> t.AbstractSet[str]:
        """Return file-level covered paths with import-time dependencies added.

        This is the file-level equivalent of _add_import_time_lines(), but avoids
        allocating and mutating one CoverageLines object per covered file for every
        test. File-level coverage only needs path membership because every covered
        file emits the same line-0 sentinel bitmap.
        """
        if not covered_file_paths:
            return covered_file_paths

        cache_key = frozenset(covered_file_paths)
        cached_paths = self._file_level_covered_paths_cache.get(cache_key)
        if cached_paths is not None:
            self._file_level_covered_paths_cache.move_to_end(cache_key)
            return cached_paths

        paths = set(covered_file_paths)
        import_time_covered = self._import_time_covered
        for root_path in cache_key:
            if root_path in import_time_covered:
                paths.update(self._get_import_time_dependency_paths(root_path))

        self._file_level_covered_paths_cache[cache_key] = frozenset(paths)
        if len(self._file_level_covered_paths_cache) > _FILE_LEVEL_COVERED_PATHS_CACHE_MAX_SIZE:
            self._file_level_covered_paths_cache.popitem(last=False)
        return paths

    class CollectInContext:
        """Own coverage data for one collection scope.

        Context copies share collector objects but have independent immutable stacks.
        Object identity distinguishes scopes during value-based context restoration;
        the shared closed flag prevents inherited contexts from writing to finished scopes.
        """

        def __init__(self, is_import_coverage: bool = False):
            self.is_import_coverage = is_import_coverage

        def __enter__(self):
            # Collector objects compare by identity, so value-based context restores
            # distinguish different collectors even when their coverage data is empty.
            self._covered_lines: defaultdict[str, CoverageLines] = defaultdict(CoverageLines)
            self._covered_files: set[str] = set()
            # Import-only mode: import edges executed within this scope (see hook_import).
            self._import_names: defaultdict[str, set[tuple[str, tuple[str, ...]]]] = defaultdict(set)
            # TODO: reopening a reused collector also reactivates contexts copied during its previous
            # scope, see the xfail test test_reused_collector_does_not_reactivate_contexts_from_its_previous_scope.
            self.closed = False
            ctx_collectors.set(ctx_collectors.get() + (self,))
            ctx_coverage_enabled.set(True)

            if self.is_import_coverage:
                ctx_is_import_coverage.set(self.is_import_coverage)

            # Python 3.14+ sys.monitoring callbacks can't see ContextVar changes,
            # so also store in thread-local as a fallback for the hook.
            if _PY_GE_314:
                _tls_coverage.stack = ctx_collectors.get()

            # For Python 3.12+, dynamically detect whether other sys.monitoring tools are
            # active and update the DISABLE optimisation flag accordingly.  Then re-enable
            # monitoring only when the optimisation is active (restart_events is global and
            # would corrupt other tools' state if called unnecessarily).
            if (instance := ModuleCodeCollector._instance) is not None:
                # Re-arm the instrumentation (sys.monitoring restart_events() on 3.12+,
                # bytecode probes below); engines decide what re-arming means per version.
                instance.arm(import_context=self.is_import_coverage)

            return self

        def __exit__(self, *args, **kwargs):
            # Closing the shared collector expires it in every inherited stack.
            self.closed = True
            stack = ctx_collectors.get()
            if stack and stack[-1] is self:
                stack = stack[:-1]
                ctx_collectors.set(stack)
            # An exit in a different context must preserve that context's collectors.
            coverage_enabled = bool(stack) and (not stack[-1].closed or _get_active_collector(stack) is not None)
            ctx_coverage_enabled.set(coverage_enabled)
            if _PY_GE_314:
                _tls_coverage.stack = stack

        def anchor_file(self, path: str) -> None:
            """Mark a file as covered in this scope without an import event.

            Import-only mode has no execution events to attribute the test's own file
            (and its package __init__ files) with, so the framework integration anchors
            them: they become the roots the import-time closure expands from.
            """
            self._covered_files.add(path)
            self._covered_lines[path].add(0)

        def get_covered_lines(self) -> dict[str, CoverageLines]:
            covered_lines = self._covered_lines
            if global_instance := ModuleCodeCollector._instance:
                if global_instance._import_only is not None:
                    global_instance._import_only.add_import_time_lines_for_context(covered_lines, self._import_names)
                else:
                    global_instance._add_import_time_lines(covered_lines)
            return covered_lines

        def get_covered_file_paths(self) -> t.AbstractSet[str]:
            # Python < 3.12 and injected child-process coverage may only update the line-oriented
            # context data. Merge those keys into the file set so file-level uploads still include
            # every file that would have been emitted by get_covered_lines().
            covered_file_paths = set(self._covered_files)
            covered_file_paths.update(self._covered_lines)
            if global_instance := ModuleCodeCollector._instance:
                if global_instance._import_only is not None:
                    return global_instance._import_only.covered_file_paths_for_context(
                        covered_file_paths, self._import_names
                    )
                return global_instance._get_covered_file_paths_with_imports(covered_file_paths)
            return covered_file_paths

    @classmethod
    def file_level_coverage_enabled(cls):
        return cls._instance is not None and cls._instance._file_level_coverage

    @classmethod
    def start_coverage(cls):
        if cls._instance is None:
            return
        cls._instance._covered_files.clear()
        cls._instance._coverage_enabled = True

    @classmethod
    def stop_coverage(cls):
        if cls._instance is None:
            return
        cls._instance._coverage_enabled = False

    @classmethod
    def coverage_enabled(cls):
        if ctx_coverage_enabled.get():
            return True
        if cls._instance is None:
            return False
        return cls._instance._coverage_enabled

    @classmethod
    def get_import_coverage_for_paths(cls, paths: t.Iterable[Path]) -> t.Optional[dict[Path, CoverageLines]]:
        """Returns import-time coverage data for the given paths"""
        coverages: dict[Path, CoverageLines] = {}
        if cls._instance is None:
            return {}
        for path in paths:
            path_str = str(path)
            if path_str in cls._instance._import_time_covered:
                coverages[path] = cls._instance._import_time_covered[path_str]

        return coverages

    @classmethod
    def coverage_enabled_in_context(cls):
        return cls._instance is not None and ctx_coverage_enabled.get()

    @classmethod
    def report_seen_lines(cls, workspace_path: Path, include_imported: bool = False):
        """Generate the same data as expected by ddtrace.ci_visibility.coverage.build_payload:

        if input_path is provided, filter files to only include that path, and make it relative to said path

        "files": [
            {
                "filename": <String>,
                "segments": [
                    [Int, Int, Int, Int, Int],  # noqa:F401
                ]
            },
            ...
        ]
        """
        if cls._instance is None:
            return []
        files = []
        covered = cls._instance._get_covered_lines(include_imported=include_imported)

        for abs_path_str, lines in covered.items():
            abs_path = Path(abs_path_str)
            relative = relative_path(abs_path, workspace_path)
            path_str = relative if relative is not None else abs_path_str

            sorted_lines = [i for i, v in enumerate(sorted(lines.to_sorted_list())) if v == 1]

            collapsed_ranges = collapse_ranges(sorted_lines)
            file_segments = []
            for file_segment in collapsed_ranges:
                file_segments.append([file_segment[0], 0, file_segment[1], 0, -1])
            files.append({"filename": path_str, "segments": file_segments})

        return files

    def _is_instrumentable_path(self, code_path: Path) -> bool:
        if _is_site_packages_path(code_path):
            return False
        if not any(is_contained(code_path, include_path) for include_path in self._include_paths):
            return False
        if any(is_contained(code_path, exclude_path) for exclude_path in self._exclude_paths):
            return False
        return True

    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        if _module is None:
            return code

        code_path = resolved_code_origin(code)

        if not self._is_instrumentable_path(code_path):
            return code

        if self._import_only_coverage:
            # No code instrumentation: the line-0 sentinel stands in for executable lines,
            # and the import-time context is still entered so the module participates in
            # the import-time dependency graph (after_import records the sentinel).
            self.lines[code.co_filename].add(0)
            if self._collect_import_coverage and self._import_only is not None:
                self._import_only.prepare_module(code, _module)
            return code

        retval = self.instrument_code(code, _module.__package__ if _module is not None else "")

        if self._collect_import_coverage:
            self._import_time_name_to_path[_module.__name__] = code.co_filename
            self._direct_import_time_dependency_paths_cache.clear()
            self._import_time_dependency_paths_cache.clear()
            self._file_level_covered_paths_cache.clear()
            module_context = self.CollectInContext(is_import_coverage=True)
            module_context.__enter__()
            self._import_time_contexts[code.co_filename] = module_context

        # Empty-module recording is instrumentation-specific (e.g. Python 3.13+
        # sys.monitoring does not fire LINE events for empty modules): the engine records it
        # after the import-time context is set up so coverage lands in the correct context.
        self._engine.on_empty_module(code, (_module.__package__ or "") if _module is not None else "")

        return retval

    def _exit_context_on_exception_hook(self, _, _module: ModuleType) -> None:
        if hasattr(_module, "__file__") and _module.__file__ in self._import_time_contexts:
            collector = self._import_time_contexts[_module.__file__]
            covered_lines = collector.get_covered_lines()
            collector.__exit__()
            if covered_lines[_module.__file__]:
                self._import_time_covered[_module.__file__].update(covered_lines[_module.__file__])
                self._import_time_dependency_paths_cache.clear()
                self._file_level_covered_paths_cache.clear()
            elif self._import_only is not None:
                self._import_only.record_module(_module)

            del self._import_time_contexts[_module.__file__]

    def after_import(self, _module: ModuleType) -> None:
        if not self._collect_import_coverage:
            return

        if hasattr(_module, "__file__") and _module.__file__ in self._import_time_contexts:
            collector = self._import_time_contexts[_module.__file__]
            covered_lines = collector.get_covered_lines()
            collector.__exit__()
            if covered_lines[_module.__file__]:
                self._import_time_covered[_module.__file__].update(covered_lines[_module.__file__])
                self._import_time_dependency_paths_cache.clear()
                self._file_level_covered_paths_cache.clear()
            elif self._import_only is not None:
                self._import_only.record_module(_module)

            del self._import_time_contexts[_module.__file__]

    def instrument_code(self, code: CodeType, package) -> CodeType:
        if is_obfuscated_code(code):
            log.warning(
                "Cannot instrument %r for coverage: code object appears to be obfuscated (e.g. by PyArmor)",
                code.co_name,
            )
            return code

        # Avoid instrumenting the same code object multiple times
        if (code, code.co_filename) in self.seen:
            return code
        self.seen.add((code, code.co_filename))

        new_code, lines = self._engine.instrument(code, code.co_filename, package)
        self.seen.add((new_code, code.co_filename))
        # Keep note of all the lines that have been instrumented. These will be
        # the ones that can be covered.
        self.lines[code.co_filename].update(lines)
        return new_code

    def _exec(self, _object, _globals=None, _locals=None, **kwargs):
        # The pytest module loader doesn't implement a get_code method so we
        # need to intercept the loading of test modules by wrapping around the
        # exec built-in function.

        module = getmodule(_object)

        new_object = (
            self.transform(_object, module)
            if isinstance(_object, CodeType) and _object.co_name == "<module>"
            else _object
        )
        # Execute the module before calling the after_import hook
        _original_exec(new_object, _globals, _locals, **kwargs)

    @classmethod
    def uninstall(cls) -> None:
        # Restore the original exec function
        try:
            import _pytest.assertion.rewrite as par

            par.exec = _original_exec
        except ImportError:
            pass

        # Restore the original __import__ (import-only mode)
        uninstall_import_hook()

        return super().uninstall()
