import dis
import sys
from types import CodeType

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.coverage.coverage_lines import CoverageLines
from ddtrace.internal.coverage.util import is_file_level_coverage_enabled


assert (3, 10) <= sys.version_info < (3, 12)  # nosec


# POC: file-level coverage via bytecode injection, mirroring _DD_COVERAGE_FILE_LEVEL on 3.12+.
# Instead of injecting a trap at every line start, only inject at:
#   1. the first line start of each code object (module first line / function first line)
#   2. the line starts containing import statements (import-dependency tracking)
# Every trap emits the line-0 sentinel (InjectionContext.file_level), which
# ModuleCodeCollector.hook() routes to hook_file()/hook_import().
_USE_FILE_LEVEL_COVERAGE = is_file_level_coverage_enabled()

_IMPORT_OPCODES = (dis.opmap["IMPORT_NAME"], dis.opmap["IMPORT_FROM"])


def _line_level_offsets(context: "InjectionContext") -> list[int]:
    return [offset for offset, _ in dis.findlinestarts(context.original_code)]


def _file_level_offsets(context: "InjectionContext") -> list[int]:
    """Offsets that get a trap in file-level mode: the first line start of the code object plus
    the line starts of every line containing an import statement.
    """
    code = context.original_code
    starts = dict(dis.findlinestarts(code))
    if not starts:
        return []
    # On 3.11, offset 0 is the RESUME opcode (reported by findlinestarts as a pseudo "line 0"
    # start) and no instrumentation may be injected before it (see instrumentation_py3_11.py).
    if sys.version_info >= (3, 11) and code.co_code[0] == dis.opmap["RESUME"]:
        candidate_starts = [offset for offset in starts if offset > 0]
        if not candidate_starts:
            # POC (3.11 accuracy): one-line code objects (lambdas, comprehensions, generator
            # expressions) and empty modules have no line start after the RESUME opcode, so the
            # usual rule would leave them un-instrumented and their execution unreported — a
            # coverage gap vs the sys.monitoring PY_START implementation on 3.12+, which fires
            # for every code object start. Fall back to the RESUME pseudo-line start at offset 0.
            # NOTE: this injects *before* RESUME, which instrumentation_py3_11.py deliberately
            # avoids; validate against generators/async before adopting (see the POC harness).
            candidate_starts = [0]
    else:
        candidate_starts = list(starts)
    first_line_offset = min(candidate_starts)

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
    if _USE_FILE_LEVEL_COVERAGE:
        injection_context = InjectionContext(code, hook, _file_level_offsets, file_level=True)
    else:
        injection_context = InjectionContext(code, hook, _line_level_offsets)

    new_code, lines = inject_invocation(injection_context, path, package)

    coverage_lines = CoverageLines()
    if not _USE_FILE_LEVEL_COVERAGE:
        # In file-level mode line-level bookkeeping (which lines can be covered) is not used,
        # matching the sys.monitoring implementation which returns an empty CoverageLines when
        # track_lines is False.
        for line in lines:
            coverage_lines.add(line)

    return new_code, coverage_lines
