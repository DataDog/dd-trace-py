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

from ddtrace.contrib.internal.coverage.patch import get_coverage_instance
from ddtrace.contrib.internal.coverage.patch import get_coverage_percentage as _get_coverage_percentage
from ddtrace.contrib.internal.coverage.patch import patch as patch_coverage
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.patch import unpatch as unpatch_coverage
from ddtrace.contrib.internal.coverage.utils import _build_path_aliases
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.coverage_lines import CoverageLines
import ddtrace.internal.coverage.installer
from ddtrace.internal.utils.paths import relative_path
from ddtrace.testing.internal.logging import catch_and_log_exceptions


log = logging.getLogger(__name__)


log = logging.getLogger(__name__)


def install_coverage(workspace_path: Path, file_level_coverage: bool = False) -> None:
    def alias_matcher(code_path: Path) -> bool:
        # Installed copies of the library under test (e.g. a wheel in a virtual
        # environment's site-packages) normally fail the collector's gates: they
        # live under site-packages and possibly outside the workspace. When a
        # coverage [paths] alias maps such a copy back to this repository, collect
        # it so the coverage report can attribute it to the repository.
        matched, mapped = _alias_mapped_path(str(code_path), str(workspace_path))
        return matched and mapped is not None

    ddtrace.internal.coverage.installer.install(
        include_paths=[workspace_path],
        collect_import_time_coverage=True,
        file_level_coverage=file_level_coverage,
        alias_matcher=alias_matcher,
    )
    ModuleCodeCollector.start_coverage()


def uninstall_coverage() -> None:
    ModuleCodeCollector.uninstall()


@functools.lru_cache(maxsize=1)
def _coverage_path_aliases() -> t.Optional[t.Any]:
    """The [paths] aliases of the active coverage configuration, or None if
    coverage.py is unavailable or no aliases are configured.

    Aliases come from the active coverage instance when one is running (e.g. a
    pytest-cov session with a custom --cov-config), and from the default
    configuration discovery otherwise.
    """
    try:
        return _build_path_aliases(get_coverage_instance())
    except Exception:
        log.debug("Could not build coverage [paths] aliases", exc_info=True)
        return None


def clear_coverage_path_caches() -> None:
    """Reset the coverage path remapping caches for a new coverage session.

    The aliases (and the per-path results) depend on the active coverage
    instance and the configuration discovered from the working directory, so
    they must not be reused across sessions that may change either.
    """
    _coverage_path_aliases.cache_clear()
    _relative_coverage_path.cache_clear()


def _alias_mapped_path(absolute_path: str, relative_to: str) -> tuple[bool, t.Optional[str]]:
    """Whether a [paths] alias matches the file, and its upload path.

    The first matching alias wins. Relative destinations are resolved against
    the working directory, and every destination is reported relative to the
    workspace; a destination outside the workspace cannot be attributed to the
    repository, so the file is dropped (matched with a None path).
    """
    aliases = _coverage_path_aliases()
    if aliases is None:
        return False, None
    for _pattern, regex, result in aliases.aliases:
        if match := regex.match(absolute_path):
            mapped = absolute_path.replace(match[0], result, 1).removeprefix("./")
            if Path(mapped).is_absolute():
                mapped_path = Path(mapped)
            else:
                mapped_path = Path(os.path.normpath(str(Path.cwd() / mapped)))
            relative = relative_path(mapped_path, Path(relative_to))
            if relative is None:
                return True, None  # mapped outside the current repository
            return True, f"/{relative}"
    return False, None


@functools.lru_cache(maxsize=65536)
def _relative_coverage_path(absolute_path: str, relative_to: str) -> t.Optional[str]:
    # Aliases take precedence so installed copies of repository files are reported
    # at their repository paths, matching what coverage combine does for reports.
    matched, mapped = _alias_mapped_path(absolute_path, relative_to)
    if matched:
        # A None mapping falls outside the repository, so the file is dropped
        # rather than uploaded with a path the backend cannot attribute.
        return mapped
    relative = relative_path(Path(absolute_path), Path(relative_to))
    if relative is None:
        return None  # covered file does not belong to current repo
    return f"/{relative}"


_FILE_LEVEL_BITMAP = bytes(CoverageLines.from_list([0]).to_bytes())


class CoverageData:
    def __init__(self) -> None:
        self._covered_lines: t.Optional[dict[str, CoverageLines]] = None
        self._covered_file_paths: t.Optional[t.Iterable[str]] = None

    def get_coverage_bitmaps(self, relative_to: Path) -> t.Iterable[tuple[str, bytes]]:
        relative_to_str = str(relative_to)

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


@contextlib.contextmanager
def coverage_collection() -> t.Generator[CoverageData, None, None]:
    with ModuleCodeCollector.CollectInContext() as coverage_collector:
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
