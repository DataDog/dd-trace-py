import coverage

from ddtrace.contrib.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.coverage.utils import is_coverage_imported
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.vendor import wrapt


log = get_logger(__name__)

_coverage_data = {}


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

    _u(coverage.Coverage.report, "report_total_pct_covered_wrapper")

    coverage._datadog_patch = False


def report_total_pct_covered_wrapper(func, instance, args: tuple, kwargs: dict):
    pct_covered = func(*args, **kwargs)
    _coverage_data[PCT_COVERED_KEY] = pct_covered
    return pct_covered
