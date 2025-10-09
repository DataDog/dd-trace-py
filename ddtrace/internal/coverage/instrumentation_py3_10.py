import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info[:2] == (3, 10)  # nosec

# Python 3.10 doesn't have RESUME instruction (introduced in 3.11)
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]


def _get_offsets_to_instrument(injection_context) -> t.List[int]:
    """Get bytecode offsets to instrument for lightweight coverage.

    LIGHTWEIGHT COVERAGE STRATEGY:
    We ONLY instrument the first executable line of each code object. This is sufficient because:

    1. For modules: First line always executes when module loads
       - Tracks that the module was imported ✅
       - Import dependencies tracked via bytecode scanning (done by inject_invocation) ✅

    2. For functions: First line executes when function is called
       - Tracks that the function ran ✅

    This is simpler and faster than "first + all imports", reducing instrumentation by ~50%.
    Import metadata is still captured by scanning bytecode for IMPORT_NAME/IMPORT_FROM and
    attaching it to the first line's hook.

    Note: Python 3.10 doesn't have RESUME instruction, so we just use the first line directly.
    """
    code = injection_context.original_code
    line_starts_list = list(dis.findlinestarts(code))
    if not line_starts_list:
        return []

    # ONLY instrument the first line (not import lines!)
    first_offset = min(o for o, _ in line_starts_list)

    return [first_offset]


def _collect_all_executable_lines(code: CodeType) -> CoverageLines:
    """Recursively collect all executable lines from a code object and its nested code objects.

    This is used for coverage reporting to show which lines COULD be covered,
    even though we only instrument the first line of each code object for performance.
    """
    coverage_lines = CoverageLines()

    # Add all lines from this code object
    for _, line in dis.findlinestarts(code):
        coverage_lines.add(line)

    # Recursively add lines from nested code objects (functions, classes, etc.)
    for const in code.co_consts:
        if isinstance(const, CodeType):
            nested_lines = _collect_all_executable_lines(const)
            coverage_lines.update(nested_lines)

    return coverage_lines


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    # For lightweight coverage, we ONLY inject a hook at the first executable line.
    # Import dependencies are still tracked because inject_invocation scans bytecode
    # for IMPORT_NAME/IMPORT_FROM and attaches metadata to the hook.
    # This approach reduces instrumentation overhead by ~50% compared to "first + all imports".
    injection_context = InjectionContext(code, hook, _get_offsets_to_instrument)
    new_code, instrumented_lines = inject_invocation(injection_context, path, package)

    # However, we still need to report ALL executable lines for accurate coverage reporting
    # This ensures that the coverage system knows what lines COULD have been covered
    coverage_lines = _collect_all_executable_lines(code)

    return new_code, coverage_lines
