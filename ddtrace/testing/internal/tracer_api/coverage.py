"""
API for code coverage collection for use by ddtrace.testing.

The rest of ddtrace.testing should only use the interface exposed in this file to set up code coverage and get
coverage data.
"""

import contextlib
import logging
from pathlib import Path
import typing as t

from ddtrace.contrib.internal.coverage.patch import get_coverage_percentage as _get_coverage_percentage
from ddtrace.contrib.internal.coverage.patch import patch as patch_coverage
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.patch import unpatch as unpatch_coverage
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.internal.coverage.code import ModuleCodeCollector
import ddtrace.internal.coverage.installer
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.testing.internal.logging import catch_and_log_exceptions


log = logging.getLogger(__name__)


log = logging.getLogger(__name__)


def install_coverage(workspace_path: Path) -> None:
    ddtrace.internal.coverage.installer.install(include_paths=[workspace_path], collect_import_time_coverage=True)
    ModuleCodeCollector.start_coverage()


class CoverageData:
    def __init__(self) -> None:
        self._covered_lines: t.Optional[dict[str, CoverageLines]] = None

    def get_coverage_bitmaps(self, relative_to: Path) -> t.Iterable[tuple[str, bytes]]:
        if not self._covered_lines:
            return

        for absolute_path, covered_lines in self._covered_lines.items():
            try:
                relative_path = Path(absolute_path).relative_to(relative_to)
            except ValueError:
                continue  # covered file does not belong to current repo

            path_str = f"/{relative_path}"
            yield path_str, covered_lines.to_bytes()


@contextlib.contextmanager
def coverage_collection() -> t.Generator[CoverageData, None, None]:
    with ModuleCodeCollector.CollectInContext() as coverage_collector:
        coverage_data = CoverageData()
        yield coverage_data
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
