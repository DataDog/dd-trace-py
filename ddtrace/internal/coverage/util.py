import functools
import os
import sys

from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


@functools.lru_cache(maxsize=4096)
def resolved_path(path_str: str) -> str:
    # Memoized realpath for coverage hot paths: instrument_module runs for every
    # code object and every coverage report resolves the same workspace per test,
    # while the symlink layout never changes mid-run. Profiling showed thousands
    # of realpath/resolve calls on repeated inputs per run; the cache turns those
    # into dict lookups.
    directory, name = os.path.split(path_str)
    if directory and name and name not in (os.curdir, os.pardir):
        # Files of one directory share its resolution, so a new file costs one lstat
        # instead of one per path component.
        resolved = os.path.join(_resolved_directory(directory), name)
        return os.path.realpath(resolved) if os.path.islink(resolved) else resolved
    return os.path.realpath(path_str)


@functools.lru_cache(maxsize=1024)
def _resolved_directory(path_str: str) -> str:
    return os.path.realpath(path_str)


def is_file_level_coverage_enabled() -> bool:
    # Python 3.12+ instruments with sys.monitoring, where file-level coverage (PY_START events) is the default and
    # avoids the cost of LINE events. Older versions default to line-level coverage.
    if sys.version_info >= (3, 12) and asbool(env.get("_DD_COVERAGE_NATIVE_LINES", "false")):
        return False
    default = "true" if sys.version_info >= (3, 12) else "false"
    return asbool(env.get("_DD_COVERAGE_FILE_LEVEL", default))


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
