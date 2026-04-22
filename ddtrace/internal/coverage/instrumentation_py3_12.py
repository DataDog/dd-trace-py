"""
Coverage instrumentation for Python 3.12+ using sys.monitoring API.

This module supports two modes:
1. Line-level coverage: Tracks which specific lines are executed (LINE events)
2. File-level coverage: Tracks which files are executed (PY_START events)

The mode is controlled by the _DD_COVERAGE_FILE_LEVEL environment variable.
"""

import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]

# Detect empty modules: the bytecode pattern varies across Python versions.
# Python 3.12-3.13: RESUME + RETURN_CONST
# Python 3.14: RESUME + LOAD_CONST + RETURN_VALUE (RETURN_CONST was removed)
# Python 3.15+: same as 3.14 but RESUME has a CACHE entry (extra 2 bytes)
# Instead of hardcoding, just compile an empty module to get the expected bytes.
EMPTY_MODULE_BYTES = compile("", "<empty>", "exec").co_code

# Check if file-level coverage is requested
_USE_FILE_LEVEL_COVERAGE = asbool(env.get("_DD_COVERAGE_FILE_LEVEL", "false"))

EVENT = sys.monitoring.events.PY_START if _USE_FILE_LEVEL_COVERAGE else sys.monitoring.events.LINE

# Store: (hook, path, import_names_by_line)
# IMPORTANT: Do not change t.Dict/t.Tuple to dict/tuple until minimum Python version is 3.11+
# Module-level dict[...]/tuple[...] in Python 3.10 affects import timing. See packages.py for details.
_CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str, t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str]]]]]] = {}  # noqa: UP006


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
    """
    Instrument code for coverage tracking using Python 3.12's monitoring API.

    This function supports two modes based on _DD_COVERAGE_FILE_LEVEL:
    - Line-level (default): Uses LINE events for detailed line-by-line coverage
    - File-level: Uses PY_START events for faster file-level coverage

    Args:
        code: The code object to instrument
        hook: The hook function to call
        path: The file path
        package: The package name

    Returns:
        Tuple of (code object, CoverageLines with instrumentable lines)

    Note: Both modes use an optimized approach where callbacks return DISABLE
    after recording, meaning each line/function is only reported once per coverage context.
    """
    coverage_tool = sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID)
    if coverage_tool is not None and coverage_tool != "datadog":
        log.debug("Coverage tool '%s' already registered, not gathering coverage", coverage_tool)
        return code, CoverageLines()

    if coverage_tool is None:
        mode = "file-level" if _USE_FILE_LEVEL_COVERAGE else "line-level"
        log.debug("Registering %s coverage tool", mode)
        _register_monitoring()

    return _instrument_with_monitoring(code, hook, path, package)


def _event_handler(code: CodeType, line: int) -> t.Literal[sys.monitoring.DISABLE]:
    """
    Callback for LINE/PY_START events.
    Returns sys.monitoring.DISABLE to improve performance.
    """
    hook_data = _CODE_HOOKS.get(code)
    if hook_data is None:
        return sys.monitoring.DISABLE

    hook, path, import_names = hook_data

    if _USE_FILE_LEVEL_COVERAGE:
        # Report file-level coverage using line 0 as a sentinel value
        # Line 0 indicates "file was executed" without specific line information
        hook((0, path, None))

        # Report any import dependencies (extracted at instrumentation time from bytecode)
        # This ensures import tracking works even though we don't fire on individual lines
        for line_num, import_name in import_names.items():
            hook((line_num, path, import_name))
    else:
        # Report the line and then disable monitoring for this specific line
        # This ensures each line is only reported once per context, even if executed multiple times (e.g., in loops)
        import_name = import_names.get(line, None)
        hook((line, path, import_name))

    # Return DISABLE to prevent future callbacks for this specific line/code
    return sys.monitoring.DISABLE


def _register_monitoring():
    """
    Register the coverage tool with the monitoring system.

    This sets up the appropriate callback based on the coverage mode.
    """
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")
    sys.monitoring.register_callback(sys.monitoring.COVERAGE_ID, EVENT, _event_handler)


def _instrument_with_monitoring(
    code: CodeType, hook: HookType, path: str, package: str
) -> tuple[CodeType, CoverageLines]:
    """
    Instrument code using either LINE events for detailed line-by-line coverage or PY_START for file-level.
    """
    # Enable local line/py_start events for the code object
    sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, EVENT)  # noqa

    track_lines = not _USE_FILE_LEVEL_COVERAGE
    # Extract import names and collect line numbers
    lines, import_names = _extract_lines_and_imports(code, package, track_lines=track_lines)

    # Recursively instrument nested code objects
    for nested_code in (_ for _ in code.co_consts if isinstance(_, CodeType)):
        _, nested_lines = instrument_all_lines(nested_code, hook, path, package)
        lines.update(nested_lines)

    # Register the hook and argument for the code object
    _CODE_HOOKS[code] = (hook, path, import_names)

    if _USE_FILE_LEVEL_COVERAGE:
        # Return CoverageLines with line 0 as sentinel to indicate file-level coverage
        # Line 0 means "file was instrumented/executed" without specific line details
        lines = CoverageLines()
        lines.add(0)
        return code, lines
    else:
        # Special case for empty modules (eg: __init__.py ):
        # Make sure line 0 is marked as executable, and add package dependency
        if not lines and code.co_name == "<module>" and code.co_code == EMPTY_MODULE_BYTES:
            lines.add(0)
            if package is not None:
                import_names[0] = (package, ("",))

    return code, lines


def _extract_lines_and_imports(
    code: CodeType, package: str, track_lines: bool = True
) -> tuple[CoverageLines, dict[int, tuple[str, tuple[str, ...]]]]:
    """
    Extract line numbers and import information from bytecode using dis.get_instructions().

    This parses the bytecode to:
    1. Collect all executable line numbers (if track_lines=True)
    2. Track IMPORT_NAME and IMPORT_FROM opcodes for dependency tracking

    Uses dis.get_instructions() which properly handles version-specific bytecode differences
    (CACHE entries, arg encoding, line number formats) across Python 3.12+.

    Args:
        code: The code object to analyze
        package: The package name for resolving relative imports
        track_lines: Whether to collect line numbers (True for LINE mode, False for PY_START mode)

    Returns:
        Tuple of (CoverageLines with executable lines, dict mapping lines to imports)
    """
    lines = CoverageLines()
    import_names: dict[int, tuple[str, tuple[str, ...]]] = {}

    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None
    line: t.Optional[int] = None
    prev_line: t.Optional[int] = None

    # Track the previous two argvals to determine import depth.
    # For IMPORT_NAME, the import depth is loaded two instructions prior:
    # Python 3.12-3.13: LOAD_CONST <level>, LOAD_CONST <fromlist>, IMPORT_NAME <name>
    # Python 3.14+: LOAD_SMALL_INT <level>, LOAD_CONST <fromlist>, IMPORT_NAME <name>
    # Using argval gives us the decoded value regardless of the opcode used.
    prev_argval: t.Any = 0
    prev_prev_argval: t.Any = 0

    for instr in dis.get_instructions(code):
        if instr.opname == "RESUME" or instr.opname == "CACHE":
            continue

        # Track line numbers.
        # Python 3.13+: line_number is an int or None for every instruction.
        # Python 3.12: line_number doesn't exist; starts_line is an int (first instruction on a line) or None.
        # Note: on 3.13+, starts_line became a boolean — do NOT use it as a line number.
        instr_line = getattr(instr, "line_number", None)
        if instr_line is None and not hasattr(instr, "line_number"):
            instr_line = instr.starts_line
        if instr_line is not None and instr_line != prev_line:
            line = instr_line
            prev_line = line
            if track_lines:
                lines.add(line)

                # Make sure that the current module is marked as depending on its own package by instrumenting the
                # first executable line
                if code.co_name == "<module>" and len(lines) == 1 and package is not None:
                    import_names[line] = (package, ("",))

        if instr.opcode == IMPORT_NAME and line is not None:
            import_depth: int = prev_prev_argval
            current_import_name = instr.argval
            # Adjust package name if the import is relative and a parent (ie: if depth is more than 1)
            current_import_package = ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package

            if line in import_names:
                import_names[line] = (
                    current_import_package,
                    tuple(list(import_names[line][1]) + [current_import_name]),
                )
            else:
                import_names[line] = (current_import_package, (current_import_name,))

        # Also track import from statements since it's possible that the "from" target is a module, eg:
        # from my_package import my_module
        # Since the package has not changed, we simply extend the previous import names with the new value
        elif instr.opcode == IMPORT_FROM and line is not None:
            import_from_name = f"{current_import_name}.{instr.argval}"
            if line in import_names:
                import_names[line] = (
                    current_import_package,
                    tuple(list(import_names[line][1]) + [import_from_name]),
                )
            else:
                import_names[line] = (current_import_package, (import_from_name,))

        # Shift argval history for import depth tracking
        prev_prev_argval = prev_argval
        prev_argval = instr.argval

    return lines, import_names
