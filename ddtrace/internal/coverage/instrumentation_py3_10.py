import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info[:2] == (3, 10)  # nosec

RESUME = dis.opmap.get("RESUME", -1)


def _get_first_executable_offset(injection_context) -> t.List[int]:
    """Get the first executable bytecode offset after RESUME instruction.
    
    For module code, we want to instrument the first line that actually executes,
    which is after the RESUME instruction. For other code objects (functions, etc.),
    we also want the first executable line after RESUME.
    """
    code = injection_context.original_code
    line_starts = list(dis.findlinestarts(code))
    if not line_starts:
        return []
    
    # Find the RESUME offset if it exists
    resume_offset = -1
    for i in range(0, len(code.co_code), 2):
        if code.co_code[i] == RESUME:
            resume_offset = i
            break
    
    # Get the first offset after RESUME
    for offset, line in line_starts:
        if offset > resume_offset:
            return [offset]
    
    # Fallback: if no offset after RESUME, use the minimum offset
    return [min(o for o, _ in line_starts)]


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
    # For lightweight coverage, we only inject hooks at the first executable line
    # This reduces overhead significantly while still tracking file-level coverage
    injection_context = InjectionContext(code, hook, _get_first_executable_offset)
    new_code, instrumented_lines = inject_invocation(injection_context, path, package)

    # However, we still need to report ALL executable lines for accurate coverage reporting
    # This ensures that the coverage system knows what lines COULD have been covered
    coverage_lines = _collect_all_executable_lines(code)

    return new_code, coverage_lines
