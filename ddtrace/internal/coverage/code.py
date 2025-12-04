from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from inspect import getmodule
import os
from pathlib import Path
import sys
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.coverage.instrumentation import instrument_all_lines
from ddtrace.internal.coverage.report import gen_json_report
from ddtrace.internal.coverage.report import print_coverage_report
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.packages import platlib_path
from ddtrace.internal.packages import platstdlib_path
from ddtrace.internal.packages import purelib_path
from ddtrace.internal.packages import stdlib_path
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.inspection import resolved_code_origin


log = get_logger(__name__)

_original_exec = exec

ctx_covered: ContextVar[t.Optional[t.List[t.DefaultDict[str, CoverageLines]]]] = ContextVar("ctx_covered", default=None)
ctx_is_import_coverage = ContextVar("ctx_is_import_coverage", default=False)
ctx_coverage_enabled = ContextVar("ctx_coverage_enabled", default=False)

# Track import dependencies per coverage context (not globally)
# This ensures that only imports executed in the current request are included
ImportNamesType = t.DefaultDict[str, t.Set[t.Tuple[str, t.Tuple[str, ...]]]]
ctx_import_names: ContextVar[t.Optional[t.List[ImportNamesType]]] = ContextVar("ctx_import_names", default=None)


def _get_ctx_covered_lines() -> t.DefaultDict[str, CoverageLines]:
    if ctx_coverage_enabled.get():
        if context_stack := ctx_covered.get():
            return context_stack[-1]
        log.debug("_get_ctx_covered_lines() called but ctx_covered stack is empty")

    return defaultdict(CoverageLines)


def _get_ctx_import_names() -> ImportNamesType:
    """Get the current context's import names (for dependency tracking)."""
    if ctx_coverage_enabled.get():
        if import_names_stack := ctx_import_names.get():
            return import_names_stack[-1]
    return defaultdict(set)


def _add_to_contexts(
    path: str,
    line: t.Optional[int] = None,
    import_name: t.Optional[t.Tuple[str, t.Tuple[str, ...]]] = None,
    add_to_all: bool = False,
) -> None:
    """Add coverage data to active contexts.

    Args:
        path: The file path
        line: Line number to add (if tracking line coverage)
        import_name: Import dependency to add (if tracking imports)
        add_to_all: If True, add to ALL contexts in the stack (for import-time coverage).
                   If False, add only to the current (top) context.

    When add_to_all=True (during module import), we add to all contexts so that:
    1. The request context gets the coverage (for the current request)
    2. The import-time context gets the coverage (for future requests)
    """
    if not ctx_coverage_enabled.get():
        return

    if add_to_all:
        # Add to ALL contexts in the stack
        if line is not None:
            if context_stack := ctx_covered.get():
                for ctx in context_stack:
                    ctx[path].add(line)
        if import_name is not None:
            if import_names_stack := ctx_import_names.get():
                for ctx in import_names_stack:
                    ctx[path].add(import_name)
    else:
        # Add to current (top) context only
        if line is not None:
            _get_ctx_covered_lines()[path].add(line)
        if import_name is not None:
            _get_ctx_import_names()[path].add(import_name)


class ModuleCodeCollector(ModuleWatchdog):
    _instance: t.Optional["ModuleCodeCollector"] = None

    def __init__(self) -> None:
        super().__init__()
        # Coverage collection configuration
        self._collect_import_coverage: bool = False
        self._include_paths: t.List[Path] = []
        # By default, exclude standard / venv paths (eg: avoids over-instrumenting cases where a virtualenv is created
        # in the root directory of a repository)
        self._exclude_paths: t.List[Path] = [stdlib_path, platstdlib_path, platlib_path, purelib_path]

        # Avoid instrumenting anything in the current module
        self._exclude_paths.append(Path(__file__).resolve().parent)

        self._coverage_enabled: bool = False
        self.seen: t.Set[t.Tuple[CodeType, str]] = set()

        # Data structures for coverage data
        self.lines: t.DefaultDict[str, CoverageLines] = defaultdict(CoverageLines)
        self.covered: t.DefaultDict[str, CoverageLines] = defaultdict(CoverageLines)

        # Import-time coverage data
        self._import_time_covered: t.DefaultDict[str, CoverageLines] = defaultdict(CoverageLines)
        self._import_time_contexts: t.Dict[str, "ModuleCodeCollector.CollectInContext"] = {}
        self._import_time_name_to_path: t.Dict[str, str] = {}
        self._import_names_by_path: t.Dict[str, t.Set[t.Tuple[str, t.Tuple[str, ...]]]] = defaultdict(set)

        # Replace the built-in exec function with our own in the pytest globals
        try:
            import _pytest.assertion.rewrite as par

            par.exec = self._exec
        except ImportError:
            pass

    @classmethod
    def install(cls, include_paths: t.Optional[t.List[Path]] = None, collect_import_time_coverage: bool = False):
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

        if collect_import_time_coverage:
            ModuleCodeCollector.register_import_exception_hook(
                lambda x: True, cls._instance._exit_context_on_exception_hook
            )

    def hook(self, arg: t.Tuple[int, str, t.Optional[t.Tuple[str, t.Tuple[str, ...]]]]):
        line, path, import_name = arg

        # Global coverage (non-context)
        if self._coverage_enabled:
            self.covered[path].add(line)

        # Context-based coverage
        is_import_time = ctx_is_import_coverage.get()

        # Add line coverage to context(s)
        _add_to_contexts(path, line=line, add_to_all=is_import_time)

        # Track import dependencies
        if import_name is not None and self._collect_import_coverage:
            # Global import tracking: only for module-level (static) imports
            if is_import_time:
                self._import_names_by_path[path].add(import_name)

            # Context-specific import tracking: for per-request dependency resolution
            _add_to_contexts(path, import_name=import_name, add_to_all=is_import_time)

    @classmethod
    def inject_coverage(
        cls,
        lines: t.Optional[t.Dict[str, CoverageLines]] = None,
        covered: t.Optional[t.Dict[str, CoverageLines]] = None,
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
            for path, path_covered in covered.items():
                if instance._coverage_enabled:
                    instance.covered[path].update(path_covered)
                if ctx_coverage_enabled.get() and ctx_covered_lines is not None:
                    ctx_covered_lines[path].update(path_covered)

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

    def _get_covered_lines(self, include_imported: bool = False) -> t.Dict[str, CoverageLines]:
        # Covered lines should always be a copy to make sure the original cannot be altered
        in_context = ctx_coverage_enabled.get()
        covered_lines = deepcopy(_get_ctx_covered_lines() if in_context else self.covered)
        if include_imported:
            # Use context-specific import names when in a coverage context
            ctx_imports = _get_ctx_import_names() if in_context else None
            self._add_import_time_lines(covered_lines, ctx_imports)

        return covered_lines

    def _add_import_time_lines(self, covered_lines, ctx_imports: t.Optional[ImportNamesType] = None):
        """Add import-time coverage for dependencies of covered files.

        This resolves import dependencies and adds coverage for imported modules.
        Dependencies come from two sources:
        - ctx_imports: Dynamic imports executed during this request (e.g., inside functions)
        - _import_names_by_path: Static module-level imports (e.g., at file top)
        """
        visited_paths: t.Set[str] = set()
        to_visit_paths = set(covered_lines.keys())

        while to_visit_paths:
            path = to_visit_paths.pop()
            if path in visited_paths:
                continue
            visited_paths.add(path)

            # Add import-time coverage for this module
            if path in self._import_time_covered:
                covered_lines[path].update(self._import_time_covered[path])

            # Collect all import dependencies for this path
            all_dependencies: t.Set[t.Tuple[str, t.Tuple[str, ...]]] = set()

            # Dynamic imports from current request
            if ctx_imports is not None:
                all_dependencies.update(ctx_imports.get(path, set()))

            # Static imports (for files with import-time coverage, or when not in a context)
            if path in self._import_time_covered or ctx_imports is None:
                all_dependencies.update(self._import_names_by_path.get(path, set()))

            # Resolve dependencies and queue them for processing
            for dependencies in all_dependencies:
                package, modules = dependencies
                for module in modules:
                    dep_fqdn = f"{package}.{module}" if package else module
                    # Only fall back to short module name for top-level imports (no package).
                    # This prevents `from thirdparty import utils` from incorrectly matching
                    # a local `utils` module when `thirdparty.utils` isn't tracked.
                    if dep_fqdn in self._import_time_name_to_path:
                        dep_name = dep_fqdn
                    elif not package and module in self._import_time_name_to_path:
                        dep_name = module
                    else:
                        dep_name = None  # Not found, skip

                    if dep_name is not None:
                        dependency_path = self._import_time_name_to_path[dep_name]
                        if dependency_path not in visited_paths:
                            to_visit_paths.add(dependency_path)

                    # Since modules can import from packages below them in the hierarchy, we may also need to find
                    # packages that were imported (eg: identifying __init__.py files). We do this by working our way
                    # from the module name to the package name "one dot at a time"
                    parent_package = dep_fqdn.split(".")[:-1]
                    while parent_package:
                        parent_package_str = ".".join(parent_package)
                        if parent_package_str in self._import_time_name_to_path:
                            dependency_path = self._import_time_name_to_path[parent_package_str]
                            if dependency_path not in visited_paths:
                                to_visit_paths.add(dependency_path)
                        if parent_package_str == package:
                            break
                        parent_package = parent_package[:-1]

    class CollectInContext:
        def __init__(self, is_import_coverage: bool = False):
            self.is_import_coverage = is_import_coverage

        def __enter__(self):
            # Initialize covered lines stack
            if ctx_covered.get() is None:
                ctx_covered.set([])
            ctx_covered.get().append(defaultdict(CoverageLines))

            # Initialize import names stack (for per-context dependency tracking)
            if ctx_import_names.get() is None:
                ctx_import_names.set([])
            ctx_import_names.get().append(defaultdict(set))

            ctx_coverage_enabled.set(True)

            if self.is_import_coverage:
                ctx_is_import_coverage.set(self.is_import_coverage)

            # For Python 3.12+, re-enable monitoring that was disabled by previous contexts
            # This ensures each test/suite gets accurate coverage data
            if sys.version_info >= (3, 12):
                sys.monitoring.restart_events()

            return self

        def __exit__(self, *args, **kwargs):
            covered_lines_stack = ctx_covered.get()
            if covered_lines_stack is None or len(covered_lines_stack) == 0:
                return

            covered_lines_stack.pop()

            # Also pop import names stack
            import_names_stack = ctx_import_names.get()
            if import_names_stack:
                import_names_stack.pop()

            # Stop coverage if we're exiting the last context
            if len(covered_lines_stack) == 0:
                ctx_coverage_enabled.set(False)

        def get_covered_lines(self, include_imported: bool = True) -> t.Dict[str, CoverageLines]:
            covered_lines = _get_ctx_covered_lines()
            if include_imported:
                if global_instance := ModuleCodeCollector._instance:
                    # Pass context-specific import names for per-request dependency tracking
                    ctx_imports = _get_ctx_import_names()
                    global_instance._add_import_time_lines(covered_lines, ctx_imports)
            return covered_lines

    @classmethod
    def start_coverage(cls):
        if cls._instance is None:
            return
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
    def get_import_coverage_for_paths(cls, paths: t.Iterable[Path]) -> t.Optional[t.Dict[Path, CoverageLines]]:
        """Returns import-time coverage data for the given paths"""
        coverages: t.Dict[Path, CoverageLines] = {}
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
            path_str = (
                str(abs_path.relative_to(workspace_path)) if abs_path.is_relative_to(workspace_path) else abs_path_str
            )

            sorted_lines = [i for i, v in enumerate(sorted(lines.to_sorted_list())) if v == 1]

            collapsed_ranges = collapse_ranges(sorted_lines)
            file_segments = []
            for file_segment in collapsed_ranges:
                file_segments.append([file_segment[0], 0, file_segment[1], 0, -1])
            files.append({"filename": path_str, "segments": file_segments})

        return files

    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        if _module is None:
            return code

        code_path = resolved_code_origin(code)

        if not any(code_path.is_relative_to(include_path) for include_path in self._include_paths):
            # Not a code object we want to instrument
            return code

        if any(code_path.is_relative_to(exclude_path) for exclude_path in self._exclude_paths):
            # Don't instrument code from standard library/site packages/etc.
            return code

        retval = self.instrument_code(code, _module.__package__ if _module is not None else "")

        if self._collect_import_coverage:
            # Warn about module name collisions (different files with same module name)
            module_name = _module.__name__
            if module_name in self._import_time_name_to_path:
                existing_path = self._import_time_name_to_path[module_name]
                if existing_path != code.co_filename:
                    log.debug(
                        "Module name collision for '%s': %s overwrites %s",
                        module_name,
                        code.co_filename,
                        existing_path,
                    )
            self._import_time_name_to_path[module_name] = code.co_filename

            # Create import-time context to capture module's coverage for future requests
            module_context = self.CollectInContext(is_import_coverage=True)
            module_context.__enter__()
            self._import_time_contexts[code.co_filename] = module_context

        return retval

    def _finalize_import_coverage(self, module_file: str) -> None:
        """Finalize import-time coverage collection for a module.

        Called when a module finishes importing (either successfully or with an exception).
        Stores the collected coverage and cleans up the import-time context.
        """
        if module_file not in self._import_time_contexts:
            return

        collector = self._import_time_contexts[module_file]
        covered_lines = collector.get_covered_lines()
        collector.__exit__()

        if covered_lines[module_file]:
            self._import_time_covered[module_file].update(covered_lines[module_file])

        del self._import_time_contexts[module_file]

    def _exit_context_on_exception_hook(self, _, _module: ModuleType) -> None:
        if hasattr(_module, "__file__"):
            self._finalize_import_coverage(_module.__file__)

    def after_import(self, _module: ModuleType) -> None:
        if self._collect_import_coverage and hasattr(_module, "__file__"):
            self._finalize_import_coverage(_module.__file__)

    def instrument_code(self, code: CodeType, package) -> CodeType:
        # Avoid instrumenting the same code object multiple times
        if (code, code.co_filename) in self.seen:
            return code
        self.seen.add((code, code.co_filename))

        new_code, lines = instrument_all_lines(code, self.hook, code.co_filename, package)
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

        return super().uninstall()
