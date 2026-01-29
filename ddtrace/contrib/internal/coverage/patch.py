import logging
from typing import Dict

import wrapt

from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.internal.coverage.data import _coverage_data
from ddtrace.contrib.internal.coverage.utils import is_coverage_loaded
from ddtrace.internal.logger import catch_and_log_exceptions
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u


try:
    import coverage
except ImportError:
    coverage = None  # type: ignore[misc,assignment]


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"coverage": "*"}


def patch():
    """
    Patch the instrumented methods from Coverage.py
    """
    if getattr(coverage, "_datadog_patch", False) or not is_coverage_loaded():
        return

    coverage._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w(coverage, "Coverage.report", report_total_pct_covered_wrapper)


def unpatch():
    """
    Undo patched instrumented methods from Coverage.py
    """
    if not getattr(coverage, "_datadog_patch", False) or not is_coverage_loaded():
        return

    _u(coverage.Coverage, "report")

    coverage._datadog_patch = False


def report_total_pct_covered_wrapper(func, instance, args: tuple, kwargs: dict):
    pct_covered = func(*args, **kwargs)
    _coverage_data[PCT_COVERED_KEY] = pct_covered
    return pct_covered


@catch_and_log_exceptions(log, ret_none=False, level=logging.WARNING)
def run_coverage_report():
    if not is_coverage_loaded():
        return
    current_coverage_object = coverage.Coverage.current()
    _coverage_data[PCT_COVERED_KEY] = current_coverage_object.report()
