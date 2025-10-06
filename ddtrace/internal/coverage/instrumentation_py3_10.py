import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info[:2] == (3, 10)  # nosec


def instrument_file_only(code: CodeType, hook: HookType, path: str, package: str) -> CodeType:
    """Lightweight instrumentation that only tracks if a file was executed, not which lines.
    
    For Python 3.10, we use the injection framework but only inject at the first line.
    The framework creates (line, path, import_name) tuples, but our defensive hook() 
    method will detect this and extract just the path for file-level tracking.
    
    This is safer than manual bytecode manipulation which can cause segfaults if jump
    offsets aren't updated correctly.
    """
    # Only instrument module-level code
    if code.co_name != "<module>":
        return code
    
    # Get the first line only
    line_starts = list(dis.findlinestarts(code))
    if not line_starts:
        return code
    
    first_line_offset = min(o for o, _ in line_starts)
    
    # Use the injection framework with only the first line
    # This creates (line, path, import_name) tuples, but the hook() method
    # will detect file-level mode and extract just the path
    injection_context = InjectionContext(code, hook, lambda _s: [first_line_offset])
    new_code, _ = inject_invocation(injection_context, path, package)
    
    return new_code


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    injection_context = InjectionContext(code, hook, lambda _s: [o for o, _ in dis.findlinestarts(_s.original_code)])
    new_code, lines = inject_invocation(injection_context, path, package)

    coverage_lines = CoverageLines()
    for line in lines:
        coverage_lines.add(line)

    return new_code, coverage_lines
