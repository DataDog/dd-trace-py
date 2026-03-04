"""
Lightweight coverage instrumentation using only sys.monitoring (Python 3.12+).

No hook indirection, no import dependency tracking — just records which lines
in which files were executed. Each line fires at most once thanks to DISABLE.

Usage::

    instrument(code)           # enable LINE events for a code object tree
    get_executable_lines()     # {filename: CoverageLines} of all instrumentable lines
    get_covered_lines()        # {filename: CoverageLines} of lines that actually ran
    reset()                    # clear all state
"""

from collections import defaultdict
import dis
import sys
from types import CodeType

from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


assert sys.version_info >= (3, 12)  # nosec

RESUME = dis.opmap["RESUME"]
RETURN_CONST = dis.opmap["RETURN_CONST"]
EMPTY_MODULE_BYTES = bytes([RESUME, 0, RETURN_CONST, 0])

_TOOL_NAME = "datadog_runtime"
_EVENT = sys.monitoring.events.LINE

# Global state — written by instrument(), read/written by _on_line().
_executable: defaultdict[str, CoverageLines] = defaultdict(CoverageLines)
_covered: defaultdict[str, CoverageLines] = defaultdict(CoverageLines)
_instrumented: set[int] = set()  # id(code) to avoid double-instrumenting
_registered: bool = False


# ── callback ────────────────────────────────────────────────────────────────


def _on_line(code: CodeType, line: int):
    """sys.monitoring LINE callback. Fires once per (code, line), then disables itself."""
    _covered[code.co_filename].add(line)
    return sys.monitoring.DISABLE


# ── public API ──────────────────────────────────────────────────────────────


def instrument(code: CodeType) -> None:
    """Enable LINE monitoring for *code* and all nested code objects recursively."""
    global _registered

    if not _registered:
        # Grab the tool slot. If another tool already holds it, bail out.
        existing = sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID)
        if existing is not None and existing != _TOOL_NAME:
            return
        if existing is None:
            sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, _TOOL_NAME)
        sys.monitoring.register_callback(sys.monitoring.COVERAGE_ID, _EVENT, _on_line)
        _registered = True

    _instrument_recursive(code)


def get_executable_lines() -> defaultdict[str, CoverageLines]:
    return _executable


def get_covered_lines() -> defaultdict[str, CoverageLines]:
    return _covered


def reset() -> None:
    """Clear all coverage state and unregister the monitoring tool."""
    global _registered
    _executable.clear()
    _covered.clear()
    _instrumented.clear()
    if _registered:
        sys.monitoring.register_callback(sys.monitoring.COVERAGE_ID, _EVENT, None)
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        _registered = False


# ── internals ───────────────────────────────────────────────────────────────


def _instrument_recursive(code: CodeType) -> None:
    if id(code) in _instrumented:
        return
    _instrumented.add(id(code))

    # Enable local LINE events for this code object.
    sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, _EVENT)

    # Collect executable lines from bytecode, skipping lines that only
    # correspond to RESUME instructions (sys.monitoring never fires LINE
    # for RESUME 0, so including them would create perpetually-uncovered
    # phantom lines — typically the ``def``/``class`` declaration line).
    filename = code.co_filename
    lines = _executable[filename]
    resume_only_lines = _resume_only_lines(code)
    for offset, _start, line in code.co_lines():
        if line is not None and line not in resume_only_lines:
            lines.add(line)

    # Handle empty modules (e.g. __init__.py with no real code).
    if not lines and code.co_name == "<module>" and code.co_code == EMPTY_MODULE_BYTES:
        lines.add(0)

    # Recurse into nested code objects (functions, classes, comprehensions, …).
    for const in code.co_consts:
        if isinstance(const, CodeType):
            _instrument_recursive(const)


def _resume_only_lines(code: CodeType) -> frozenset[int]:
    """Return the set of source lines whose *only* bytecode is RESUME.

    These lines correspond to ``def`` / ``class`` / ``async def`` declarations.
    sys.monitoring never fires a LINE event for ``RESUME 0``, so marking them
    executable would make them appear perpetually uncovered.
    """
    resume_lines: set[int] = set()
    non_resume_lines: set[int] = set()

    co_code = code.co_code
    for offset, _, line in code.co_lines():
        if line is None:
            continue
        opcode = co_code[offset]
        if opcode == RESUME:
            resume_lines.add(line)
        else:
            non_resume_lines.add(line)

    return frozenset(resume_lines - non_resume_lines)
