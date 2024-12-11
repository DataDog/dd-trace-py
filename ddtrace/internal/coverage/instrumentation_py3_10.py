import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.injection import HookType
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 10) and sys.version_info < (3, 11)  # nosec


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    injection_context = InjectionContext(code, hook, lambda _: [o for o, _ in dis.findlinestarts(code)])
    new_code, lines = inject_invocation(injection_context, path, package)

    coverage_lines = CoverageLines()
    for line in lines:
        coverage_lines.add(line)

    return new_code, coverage_lines
