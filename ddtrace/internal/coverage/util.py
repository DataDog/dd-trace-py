import functools
import os
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


@functools.lru_cache(maxsize=4096)
def resolved_path(path_str: str) -> str:
    # Memoized realpath for coverage hot paths: instrument_module runs for every
    # code object and every coverage report resolves the same workspace per test,
    # while the symlink layout never changes mid-run. Profiling showed thousands
    # of realpath/resolve calls on repeated inputs per run; the cache turns those
    # into dict lookups.
    return os.path.realpath(path_str)


def is_file_level_coverage_enabled() -> bool:
    # Python 3.12+ instruments with sys.monitoring, where file-level coverage (PY_START events) is the default and
    # avoids the cost of LINE events. Older versions default to line-level coverage.
    default = "true" if sys.version_info >= (3, 12) else "false"
    return asbool(env.get("_DD_COVERAGE_FILE_LEVEL", default))


def is_import_data_enabled() -> bool:
    # Private opt-out from import-time coverage data in per-test coverage reports. Import-time
    # data is what lets test skipping select tests that merely import changed code, so it is
    # enabled by default: disabling it trades skipping correctness for per-test overhead.
    return asbool(env.get("_DD_COVERAGE_INCLUDE_IMPORT_DATA", "true"))


_import_data_disabled_warned = False


def warn_import_data_disabled() -> None:
    """Log once per process when a reporting path drops import-time coverage data.

    Called from every reporting path that honors the opt-out (context expansion and the
    unittest internal-coverage reporter), so the warning fires exactly once regardless of
    which path reports first.
    """
    global _import_data_disabled_warned
    if _import_data_disabled_warned:
        return
    _import_data_disabled_warned = True
    log.warning(
        "Import-time coverage data is disabled (_DD_COVERAGE_INCLUDE_IMPORT_DATA=false): per-test coverage "
        "reports will not include imported files, and test skipping may miss tests that import, but do not "
        "execute, changed code"
    )


def collapse_ranges(numbers: list[int]) -> list[tuple[int, int]]:
    # This function turns an ordered list of numbers into a list of ranges.
    # For example, [1, 2, 3, 5, 6, 7, 9] becomes [(1, 3), (5, 7), (9, 9)]
    if not numbers:
        return []
    ranges = []
    start = end = numbers[0]
    for number in numbers[1:]:
        if number == end + 1:
            end = number
        else:
            ranges.append((start, end))
            start = end = number

    ranges.append((start, end))

    return ranges
