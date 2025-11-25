"""
API for code coverage collection for use by ddtrace.testing.

The rest of ddtrace.testing should only use the interface exposed in this file to set up code coverage and get coverage data.
"""

import contextlib
from pathlib import Path
import typing as t

from ddtrace.internal.coverage.code import ModuleCodeCollector
import ddtrace.internal.coverage.installer
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


def install_coverage(workspace_path: Path) -> None:
    ddtrace.internal.coverage.installer.install(include_paths=[workspace_path], collect_import_time_coverage=True)
    ModuleCodeCollector.start_coverage()  # type: ignore[no-untyped-call]


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
