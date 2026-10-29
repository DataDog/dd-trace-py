import sys

from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


def is_file_level_coverage_enabled() -> bool:
    # File-level coverage is the default on every supported version: Python 3.12+ instruments
    # with sys.monitoring (PY_START events), avoiding the cost of LINE events; Python 3.10/3.11
    # use the bytecode-injection file-level probes (see instrumentation_py3_10.py). 3.9 has no
    # file-level implementation, so the flag is forced off there rather than silently collecting
    # nothing.
    if sys.version_info < (3, 10):
        return False
    return asbool(env.get("_DD_COVERAGE_FILE_LEVEL", "true"))


def is_import_only_coverage_enabled() -> bool:
    # Import-only coverage collects file-level coverage exclusively from import events (a
    # builtins.__import__ wrapper, see import_hook_instrumentation.py): no code object is
    # instrumented at all. It implies file-level semantics, and it needs the import-time
    # dependency graph (the closure expansion is the attribution mechanism for module-level
    # imports), so both are forced on at install time.
    return asbool(env.get("_DD_COVERAGE_IMPORT_ONLY", "false"))


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
