import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info[:2] == (3, 10)  # nosec


def _get_offsets_to_instrument(injection_context) -> t.List[int]:
    """For lightweight coverage, only instrument the first executable line."""
    code = injection_context.original_code
    line_starts_list = list(dis.findlinestarts(code))
    if not line_starts_list:
        return []
    return [min(o for o, _ in line_starts_list)]


def _collect_all_executable_lines(code: CodeType) -> CoverageLines:
    """Recursively collect all executable lines from a code object."""
    coverage_lines = CoverageLines()
    for _, line in dis.findlinestarts(code):
        coverage_lines.add(line)
    for const in code.co_consts:
        if isinstance(const, CodeType):
            coverage_lines.update(_collect_all_executable_lines(const))
    return coverage_lines


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    injection_context = InjectionContext(code, hook, _get_offsets_to_instrument)
    new_code, _ = inject_invocation(injection_context, path, package)
    coverage_lines = _collect_all_executable_lines(code)
    return new_code, coverage_lines
