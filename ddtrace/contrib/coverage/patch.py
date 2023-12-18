import os
from typing import List

import coverage

from ddtrace.contrib.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.coverage.data import _coverage_data
from ddtrace.contrib.coverage.data import _original_sys_argv_command
from ddtrace.contrib.coverage.utils import is_coverage_imported
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor import wrapt


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return ""


def patch():
    """
    Patch the instrumented methods from Coverage.py
    """
    if getattr(coverage, "_datadog_patch", False) or not is_coverage_imported():
        return

    coverage._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w(coverage, "Coverage.report", report_total_pct_covered_wrapper)


def unpatch():
    """
    Undo patched instrumented methods from Coverage.py
    """
    if not getattr(coverage, "_datadog_patch", False) or not is_coverage_imported():
        return

    _u(coverage.Coverage, "report")

    coverage._datadog_patch = False


def report_total_pct_covered_wrapper(func, instance, args: tuple, kwargs: dict):
    pct_covered = func(*args, **kwargs)
    _coverage_data[PCT_COVERED_KEY] = pct_covered
    return pct_covered


def run_coverage_report():
    try:
        current_coverage_object = coverage.Coverage.current()
        _coverage_data[PCT_COVERED_KEY] = current_coverage_object.report()
    except Exception:
        log.warning("An exception occurred when running a coverage report")


def _is_coverage_patched():
    return hasattr(coverage, "_datadog_patch") and coverage._datadog_patch


def _command_invokes_coverage_run(sys_argv_command: List[str]) -> bool:
    return "coverage run -m" in " ".join(sys_argv_command)


def _is_coverage_invoked_by_coverage_run() -> bool:
    if os.environ.get("COVERAGE_RUN", False):
        return True
    return _command_invokes_coverage_run(_original_sys_argv_command)
