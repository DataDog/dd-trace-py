import sys

from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


def is_file_level_coverage_enabled() -> bool:
    # Python 3.12+ instruments with sys.monitoring, where file-level coverage (PY_START events) is the default and
    # avoids the cost of LINE events. Older versions default to line-level coverage.
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
