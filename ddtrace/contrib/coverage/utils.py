import os
import sys
from typing import List

from ddtrace.contrib.coverage.data import _original_sys_argv_command


def is_coverage_imported() -> bool:
    return "coverage" in sys.modules


def _is_coverage_patched():
    if not is_coverage_imported():
        return False
    import coverage

    return hasattr(coverage, "_datadog_patch") and coverage._datadog_patch


def _command_invokes_coverage_run(sys_argv_command: List[str]) -> bool:
    return "coverage run -m" in " ".join(sys_argv_command)


def _is_coverage_invoked_by_coverage_run() -> bool:
    if os.environ.get("COVERAGE_RUN", False):
        return True
    return _command_invokes_coverage_run(_original_sys_argv_command)
