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

    For lightweight coverage, we instrument:
    1. The first executable line - to track that the module was loaded
    2. All lines with IMPORT_NAME/IMPORT_FROM - to track import dependencies

    This ensures we capture import-time dependencies while minimizing overhead.

    Note: Python 3.10 doesn't have RESUME instruction, so we just use the first line.
    """
    code = injection_context.original_code
    line_starts_list = list(dis.findlinestarts(code))
    if not line_starts_list:
        return []

    line_starts_dict = dict(line_starts_list)

    # In Python 3.10, there's no RESUME instruction, so we just use the first line
    first_offset = min(o for o, _ in line_starts_list)

    # Find all line start offsets that contain IMPORT_NAME or IMPORT_FROM opcodes
    # We need to scan the bytecode and map each import opcode to its line's start offset
    import_line_offsets = set()
    current_line_offset = None

    for i in range(0, len(code.co_code), 2):
        # Update current line if we hit a line start
        if i in line_starts_dict:
            current_line_offset = i

        # Check if this opcode is an import
        opcode = code.co_code[i]
        if opcode in (IMPORT_NAME, IMPORT_FROM) and current_line_offset is not None:
            import_line_offsets.add(current_line_offset)

    # Combine first offset with import line offsets
    offsets_to_instrument = {first_offset} | import_line_offsets

    return sorted(list(offsets_to_instrument))


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
    # For lightweight coverage, we inject hooks at:
    # 1. The first executable line (to track module loading)
    # 2. All import lines (to track import dependencies)
    # This reduces overhead while maintaining import-time coverage accuracy
    injection_context = InjectionContext(code, hook, _get_offsets_to_instrument)
    new_code, instrumented_lines = inject_invocation(injection_context, path, package)

    # However, we still need to report ALL executable lines for accurate coverage reporting
    # This ensures that the coverage system knows what lines COULD have been covered
    coverage_lines = _collect_all_executable_lines(code)

    return new_code, coverage_lines
