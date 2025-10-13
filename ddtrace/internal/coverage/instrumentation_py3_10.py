import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import find_lines_to_instrument
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info[:2] == (3, 10)  # nosec


def instrument_all_lines(
    code: CodeType, hook: HookType, path: str, package: str, file_level: bool = True
) -> t.Tuple[CodeType, CoverageLines]:
    """
    Instrument code for coverage tracking.

    Args:
        code: The code object to instrument
        hook: The hook function to call
        path: The file path
        package: The package name
        file_level: If True, only instrument first line + import lines (minimal overhead).
                     If False, instrument all executable lines (full coverage).
    """
    if file_level:
        # Minimal instrumentation: only first line + import lines using unified function
        injection_context = InjectionContext(
            code,
            hook,
            lambda ctx: sorted(
                find_lines_to_instrument(
                    ctx.original_code, dict(dis.findlinestarts(ctx.original_code)), resume_offset=-1
                )
            ),
        )
    else:
        # Full instrumentation: all lines
        injection_context = InjectionContext(
            code, hook, lambda _s: [o for o, _ in dis.findlinestarts(_s.original_code)]
        )

    new_code, lines = inject_invocation(injection_context, path, package)

    coverage_lines = CoverageLines()
    for line in lines:
        coverage_lines.add(line)

    return new_code, coverage_lines
