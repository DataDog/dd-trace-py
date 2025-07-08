import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 13)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
RESUME = dis.opmap["RESUME"]
RETURN_CONST = dis.opmap["RETURN_CONST"]
EMPTY_MODULE_BYTES = bytes([RESUME, 0, RETURN_CONST, 0])


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    # No-op
    return code, CoverageLines()
