"""
API for code coverage collection for use by ddtrace.testing.

The rest of ddtrace.testing should only use the interface exposed in this file to set up code coverage and get
coverage data.
"""

import contextlib
import functools
import logging
import os
from pathlib import Path
import typing as t

from ddtrace.contrib.internal.coverage.patch import get_coverage_percentage as _get_coverage_percentage
from ddtrace.contrib.internal.coverage.patch import patch as patch_coverage
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.patch import unpatch as unpatch_coverage
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.internal.coverage import native_lines
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.coverage_lines import CoverageLines
import ddtrace.internal.coverage.installer
from ddtrace.internal.coverage.util import resolved_path
from ddtrace.internal.utils.paths import relative_path
from ddtrace.testing.internal.logging import catch_and_log_exceptions


log = logging.getLogger(__name__)


log = logging.getLogger(__name__)


def install_coverage(workspace_path: Path, file_level_coverage: bool = False) -> None:
    native_lines.set_report_root(resolved_path(str(workspace_path)))
    ddtrace.internal.coverage.installer.install(
        include_paths=[workspace_path],
        collect_import_time_coverage=True,
        file_level_coverage=file_level_coverage,
    )
    ModuleCodeCollector.start_coverage()


def uninstall_coverage() -> None:
    ModuleCodeCollector.uninstall()


@functools.lru_cache(maxsize=65536)
def _relative_coverage_path(absolute_path: str, relative_to: str) -> t.Optional[str]:
    relative = relative_path(Path(absolute_path), Path(relative_to))
    if relative is None:
        return None  # covered file does not belong to current repo
    return f"/{relative}"


@functools.lru_cache(maxsize=8)
def _resolved_workspace(workspace: Path) -> str:
    # Resolved once: the workspace is the same for every test, and its symlinks do not change mid-run.
    return resolved_path(str(workspace))


_FILE_LEVEL_BITMAP = bytes(CoverageLines.from_list([0]).to_bytes())


class CoverageData:
    def __init__(self) -> None:
        self._covered_lines: t.Optional[dict[str, CoverageLines]] = None
        self._covered_file_paths: t.Optional[t.Iterable[str]] = None
        self._native: t.Any = None

    def get_coverage_bitmaps(self, relative_to: Path) -> t.Iterable[tuple[str, bytes]]:
        if self._native is not None:
            return _native_bitmaps(self._native, _resolved_workspace(relative_to))
        return self._get_coverage_bitmaps(relative_to)

    def get_packed_files(self, relative_to: Path, keys: tuple[str, str]) -> t.Optional[tuple[int, bytes]]:
        """The files get_coverage_bitmaps() returns, with their count, packed as a msgpack array of
        maps {keys[0]: path, keys[1]: bitmap}; None when only get_coverage_bitmaps() can build them.
        """
        native = self._native
        if native is None or _resolved_workspace(relative_to) != native_lines.report_root():
            return None
        return t.cast(tuple[int, bytes], native.pack(*keys))

    def _get_coverage_bitmaps(self, relative_to: Path) -> t.Iterable[tuple[str, bytes]]:
        # Canonicalize the workspace: instrumentation records covered paths under
        # realpath(co_filename), so a symlinked CI workspace (e.g. Jenkins
        # WORKSPACE) must be resolved to match, or files recorded under the real
        # path are silently dropped from uploads. Memoized because this runs per
        # test with the same workspace.
        relative_to_str = resolved_path(str(relative_to))

        if self._covered_file_paths is not None:
            for absolute_path in self._covered_file_paths:
                path_str = _relative_coverage_path(absolute_path, relative_to_str)
                if path_str is not None:
                    yield path_str, _FILE_LEVEL_BITMAP
            return

        if not self._covered_lines:
            return

        for absolute_path, covered_lines in self._covered_lines.items():
            path_str = _relative_coverage_path(absolute_path, relative_to_str)
            if path_str is not None:
                yield path_str, covered_lines.to_bytes()


def _native_bitmaps(native: t.Any, workspace: str) -> list[tuple[str, bytes]]:
    """The test's own lines, the lines of the suite-scope fixture setups it depends on, and the
    initialiser lines of every file either touched, by path relative to workspace.
    """
    bitmaps: list[tuple[str, bytes]] = native.bitmaps(True)
    root = native_lines.report_root()
    if root is None or workspace == root:
        return bitmaps
    # The collector names files relative to the root it was installed with.
    rebased = []
    for report_path, bitmap in bitmaps:
        path_str = _relative_coverage_path(os.path.join(root, report_path), workspace)
        if path_str is not None:
            rebased.append((path_str, bitmap))
    return rebased


def get_shared_coverage_bitmaps() -> dict[str, list[tuple[str, bytes]]]:
    """The lines this process ran outside every test, in three parts.

    init: initialiser lines (module and class bodies); suiteSetup: suite-scope fixture setups;
    unowned: everything else, such as session fixtures, suite teardown and work that carries no test.
    Empty unless native line collection is enabled.
    """
    return native_lines.shared()


def native_line_coverage_requested() -> bool:
    return native_lines.requested()


def native_line_coverage_enabled() -> bool:
    return native_lines.enabled()


def new_fixture_coverage_context() -> t.Any:
    return native_lines.new_context(fixture=True)


def get_native_coverage_context() -> t.Any:
    return native_lines.ctx_native.get()


def set_native_coverage_context(context: t.Any) -> None:
    native_lines.ctx_native.set(context)


class _NativeCollection:
    """Runs one test in its own native coverage context.

    Native collection needs only its own context: the generic collector stack serves the Python
    hooks, which native collection does not register. A class rather than a generator, because
    it runs for every test.
    """

    __slots__ = ("_data", "_context", "_previous")

    def __init__(self) -> None:
        self._data = CoverageData()
        self._context = native_lines.new_context()
        self._previous: t.Any = None

    def __enter__(self) -> CoverageData:
        # Entering re-arms the lines disabled since the previous push.
        self._previous = self._context.enter()
        return self._data

    def __exit__(self, exc_type: t.Any, exc_value: t.Any, tb: t.Any) -> None:
        self._context.exit(self._previous)
        self._previous = None
        # The payload is built after the test finishes, from the native bitmaps.
        self._data._native = self._context


def coverage_collection(test_file: t.Optional[Path] = None) -> t.ContextManager[CoverageData]:
    if native_lines.enabled():
        return _NativeCollection()
    return _collection_in_context(test_file)


@contextlib.contextmanager
def _collection_in_context(test_file: t.Optional[Path]) -> t.Generator[CoverageData, None, None]:
    with ModuleCodeCollector.CollectInContext(test_file=test_file) as coverage_collector:
        coverage_data = CoverageData()
        yield coverage_data
        if ModuleCodeCollector.file_level_coverage_enabled():
            coverage_data._covered_file_paths = coverage_collector.get_covered_file_paths()
        else:
            coverage_data._covered_lines = coverage_collector.get_covered_lines()


def install_coverage_percentage():
    """
    Patch coverage.py to obtain coverage percentage from pytest-cov.
    """
    patch_coverage()


def uninstall_coverage_percentage():
    """
    Undo patching of coverage.py.
    """
    unpatch_coverage()


@catch_and_log_exceptions()
def get_coverage_percentage(pytest_cov_status: bool) -> t.Optional[float]:
    """
    Retrieve coverage percentage collected during a pytest-cov test session, if available.

    This retrieves the coverage percentage from coverage.py patching, coverage report upload,
    or generates a report if using 'coverage run' without pytest-cov.
    """
    # Generate report if using coverage run without pytest-cov
    if not pytest_cov_status and _is_coverage_invoked_by_coverage_run() and _is_coverage_patched():
        run_coverage_report()

    # Get cached percentage (works for pytest-cov, coverage run, and coverage report upload)
    lines_pct_value = _get_coverage_percentage()

    if lines_pct_value is None:
        log.debug("Unable to retrieve coverage data for the session span")
        return None

    if not isinstance(lines_pct_value, (float, int)):
        log.warning(
            "Unexpected format for total covered percentage: type=%s.%s, value=%r",
            type(lines_pct_value).__module__,
            type(lines_pct_value).__name__,
            lines_pct_value,
        )
        return None

    log.debug("Code coverage: %s%%", lines_pct_value)
    return float(lines_pct_value)
