import dis
import sys
from types import CodeType

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.coverage.coverage_lines import CoverageLines
from ddtrace.internal.coverage.util import is_file_level_coverage_enabled


# The bytecode-injection framework (ddtrace.internal.bytecode_injection.core) supports Python 3.10
# and 3.11, so this module serves both versions for both coverage modes. Line-level coverage on
# 3.11 was historically served by a separate hand-rolled implementation (instrumentation_py3_11,
# since removed); its RESUME-handling rules live on in _valid_line_start_offsets() below.
assert (3, 10) <= sys.version_info < (3, 12)  # nosec


# File-level coverage via bytecode injection, mirroring _DD_COVERAGE_FILE_LEVEL on 3.12+.
# Instead of injecting a trap at every line start, only inject at:
#   1. the first line start of each code object (module first line / function first line)
#   2. the line starts containing import statements (import-dependency tracking)
# Every trap emits the line-0 sentinel (InjectionContext.file_level), which
# ModuleCodeCollector.hook() routes to hook_file()/hook_import().
USE_FILE_LEVEL_COVERAGE = is_file_level_coverage_enabled()

_IMPORT_OPCODES = (dis.opmap["IMPORT_NAME"], dis.opmap["IMPORT_FROM"])
# RESUME only exists on Python 3.11+; -1 never matches an opcode byte, and _first_resume_offset
# is only consulted on 3.11+ anyway.
_RESUME = dis.opmap.get("RESUME", -1)


def _first_resume_offset(code: CodeType) -> int:
    """Offset of the first RESUME opcode, or -1 if the code object has none.

    On Python 3.11+ every code object resumes with RESUME after a (possibly empty) prologue:
    plain functions and modules have it at offset 0, while async functions and generators
    begin with RETURN_GENERATOR first. Line-start offsets at or before it are pseudo-starts
    (e.g. the ``def`` line reported at offset 0) and must not be instrumented — matching the
    rule of the hand-rolled 3.11 implementation this module replaced.
    """
    co_code = code.co_code
    for offset in range(0, len(co_code), 2):
        if co_code[offset] == _RESUME:
            return offset
    return -1


def _valid_line_start_offsets(code: CodeType) -> list[int]:
    """Line-start offsets that are valid trap injection points.

    On Python 3.11+, ``dis.findlinestarts`` reports pseudo line-starts at or before the RESUME
    opcode ("line 0" for modules, the ``def`` line for async functions and generators); no
    instrumentation is placed before the first real line start — except when the code object
    has no other line start: one-line code objects (lambdas, comprehensions, generator
    expressions) and empty modules then fall back to offset 0 so their execution is still
    reported. This keeps parity with the sys.monitoring PY_START events used on 3.12+, which
    fire for every code object start. On Python 3.10 there is no RESUME opcode and every line
    start is a valid injection point.
    """
    starts = [offset for offset, _ in dis.findlinestarts(code)]
    if sys.version_info >= (3, 11) and starts:
        resume_offset = _first_resume_offset(code)
        if resume_offset >= 0:
            valid = [offset for offset in starts if offset > resume_offset]
            return valid or [0]
    return starts


def _line_level_offsets(context: "InjectionContext") -> list[int]:
    return _valid_line_start_offsets(context.original_code)


def _file_level_offsets(context: "InjectionContext") -> list[int]:
    """Offsets that get a trap in file-level mode: the first line start of the code object plus
    the line starts of every line containing an import statement.
    """
    code = context.original_code
    candidates = _valid_line_start_offsets(code)
    if not candidates:
        return []
    first_line_offset = min(candidates)

    starts = dict(dis.findlinestarts(code))
    import_line_offsets: set[int] = set()
    current_line_offset = first_line_offset
    co_code = code.co_code
    for offset in range(0, len(co_code), 2):
        if offset in starts and offset > first_line_offset:
            current_line_offset = offset
        if co_code[offset] in _IMPORT_OPCODES and current_line_offset >= first_line_offset:
            import_line_offsets.add(current_line_offset)

    return sorted({first_line_offset} | import_line_offsets)


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
    offsets_callback = _file_level_offsets if USE_FILE_LEVEL_COVERAGE else _line_level_offsets
    injection_context = InjectionContext(code, hook, offsets_callback, file_level=USE_FILE_LEVEL_COVERAGE)

    new_code, lines = inject_invocation(injection_context, path, package)

    coverage_lines = CoverageLines()
    if not USE_FILE_LEVEL_COVERAGE:
        # In file-level mode line-level bookkeeping (which lines can be covered) is not used,
        # matching the sys.monitoring implementation which returns an empty CoverageLines when
        # track_lines is False.
        for line in lines:
            coverage_lines.add(line)

    return new_code, coverage_lines
