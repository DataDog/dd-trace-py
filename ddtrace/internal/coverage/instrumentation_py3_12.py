"""
Coverage instrumentation for Python 3.12+ using sys.monitoring API.

This module supports two modes:
1. Line-level coverage: Tracks which specific lines are executed (LINE events)
2. File-level coverage: Tracks which files are executed (PY_START events)

The mode is controlled by the _DD_COVERAGE_FILE_LEVEL environment variable.
"""

import dis
import os
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
RESUME = dis.opmap["RESUME"]
RETURN_CONST = dis.opmap["RETURN_CONST"]
EMPTY_MODULE_BYTES = bytes([RESUME, 0, RETURN_CONST, 0])

# Check if file-level coverage is requested
_USE_FILE_LEVEL_COVERAGE = asbool(os.getenv("_DD_COVERAGE_FILE_LEVEL", "false"))

EVENT = sys.monitoring.events.PY_START if _USE_FILE_LEVEL_COVERAGE else sys.monitoring.events.LINE

# Store: (hook, path, import_names_by_line)
_CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str, t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str]]]]]] = {}


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
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
) -> t.Tuple[CodeType, CoverageLines]:
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
) -> t.Tuple[CoverageLines, t.Dict[int, t.Tuple[str, t.Tuple[str, ...]]]]:
    """
    Extract line numbers and import information from bytecode.

    This parses the bytecode to:
    1. Collect all executable line numbers (if track_lines=True)
    2. Track IMPORT_NAME and IMPORT_FROM opcodes for dependency tracking

    Args:
        code: The code object to analyze
        package: The package name for resolving relative imports
        track_lines: Whether to collect line numbers (True for LINE mode, False for PY_START mode)

    Returns:
        Tuple of (CoverageLines with executable lines, dict mapping lines to imports)
    """
    lines = CoverageLines()
    import_names: t.Dict[int, t.Tuple[str, t.Tuple[str, ...]]] = {}

    # The previous two arguments are kept in order to track the depth of the IMPORT_NAME
    # For example, from ...package import module
    current_arg: int = 0
    previous_arg: int = 0
    _previous_previous_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    # Track line numbers
    linestarts = dict(dis.findlinestarts(code))
    line: t.Optional[int] = None

    ext: list[bytes] = []
    code_iter = iter(enumerate(code.co_code))
    try:
        while True:
            offset, opcode = next(code_iter)
            _, arg = next(code_iter)

            if opcode == RESUME:
                continue

            if offset in linestarts:
                line = linestarts[offset]
                # Skip if line is None (bytecode that doesn't map to a specific source line)
                if line is not None and track_lines:
                    lines.add(line)

                    # Make sure that the current module is marked as depending on its own package by instrumenting the
                    # first executable line
                    if code.co_name == "<module>" and len(lines) == 1 and package is not None:
                        import_names[line] = (package, ("",))

            if opcode is EXTENDED_ARG:
                ext.append(arg)
                continue
            else:
                _previous_previous_arg = previous_arg
                previous_arg = current_arg
                current_arg = int.from_bytes([*ext, arg], "big", signed=False)
                ext.clear()

            if opcode == IMPORT_NAME and line is not None:
                import_depth: int = code.co_consts[_previous_previous_arg]
                current_import_name: str = code.co_names[current_arg]
                # Adjust package name if the import is relative and a parent (ie: if depth is more than 1)
                current_import_package: str = (
                    ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package
                )

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
            if opcode == IMPORT_FROM and line is not None:
                import_from_name = f"{current_import_name}.{code.co_names[current_arg]}"
                if line in import_names:
                    import_names[line] = (
                        current_import_package,
                        tuple(list(import_names[line][1]) + [import_from_name]),
                    )
                else:
                    import_names[line] = (current_import_package, (import_from_name,))

    except StopIteration:
        pass

    return lines, import_names
