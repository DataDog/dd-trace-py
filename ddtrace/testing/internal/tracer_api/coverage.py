"""
API for code coverage collection for use by ddtrace.testing.

The rest of ddtrace.testing should only use the interface exposed in this file to set up code coverage and get
coverage data.
"""

import contextlib
import logging
from pathlib import Path
import typing as t

from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.internal.coverage.data import _coverage_data
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
        self._covered_lines: t.Optional[t.Dict[str, CoverageLines]] = None

    def get_coverage_bitmaps(self, relative_to: Path) -> t.Iterable[t.Tuple[str, bytes]]:
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
    """
    invoked_by_coverage_run_status = _is_coverage_invoked_by_coverage_run()
    if _is_coverage_patched() and (pytest_cov_status or invoked_by_coverage_run_status):
        if invoked_by_coverage_run_status and not pytest_cov_status:
            run_coverage_report()

        lines_pct_value = _coverage_data.get(PCT_COVERED_KEY, None)
        if lines_pct_value is None:
            log.debug("Unable to retrieve coverage data for the session span")
        elif not isinstance(lines_pct_value, (float, int)):
            t = type(lines_pct_value)
            log.warning(
                "Unexpected format for total covered percentage: type=%s.%s, value=%r",
                t.__module__,
                t.__name__,
                lines_pct_value,
            )
        else:
            log.debug("Code coverage: %s%%", lines_pct_value)
            return lines_pct_value

    return None
