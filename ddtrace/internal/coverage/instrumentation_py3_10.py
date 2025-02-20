import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info[:2] == (3, 10)  # nosec


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    injection_context = InjectionContext(code, hook, lambda _s: [o for o, _ in dis.findlinestarts(_s.original_code)])
    new_code, lines = inject_invocation(injection_context, path, package)

    coverage_lines = CoverageLines()
    for line in lines:
        coverage_lines.add(line)

    return new_code, coverage_lines
