import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
RESUME = dis.opmap["RESUME"]
RETURN_CONST = dis.opmap["RETURN_CONST"]
EMPTY_MODULE_BYTES = bytes([RESUME, 0, RETURN_CONST, 0])

# Store: (hook, path, import_names_by_line)
_CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str, t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str]]]]]] = {}


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    """
    Instrument code for coverage tracking using Python 3.12's monitoring API.

    Args:
        code: The code object to instrument
        hook: The hook function to call
        path: The file path
        package: The package name

    Note: Python 3.12+ uses an optimized approach where each line callback returns DISABLE
    after recording. This means:
    - Each line is only reported once per coverage context (test/suite)
    - No overhead for repeated line executions (e.g., in loops)
    - Full line-by-line coverage data is captured
    """
    coverage_tool = sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID)
    if coverage_tool is not None and coverage_tool != "datadog":
        log.debug("Coverage tool '%s' already registered, not gathering coverage", coverage_tool)
        return code, CoverageLines()

    if coverage_tool is None:
        log.debug("Registering code coverage tool")
        _register_monitoring()

    return _instrument_all_lines_with_monitoring(code, hook, path, package)


def _line_event_handler(code: CodeType, line: int) -> t.Literal[sys.monitoring.DISABLE]:
    hook_data = _CODE_HOOKS.get(code)
    if hook_data is None:
        return sys.monitoring.DISABLE

    hook, path, import_names = hook_data

    # Report the line and then disable monitoring for this specific line
    # This ensures each line is only reported once per context, even if executed multiple times (e.g., in loops)
    import_name = import_names.get(line, None)
    hook((line, path, import_name))

    # Return DISABLE to prevent future callbacks for this specific line
    # This provides full line coverage with minimal overhead
    return sys.monitoring.DISABLE


def _register_monitoring():
    """
    Register the coverage tool with the low-impact monitoring system.
    """
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")

    # Register the line callback
    sys.monitoring.register_callback(
        sys.monitoring.COVERAGE_ID, sys.monitoring.events.LINE, _line_event_handler
    )  # noqa


def _instrument_all_lines_with_monitoring(
    code: CodeType, hook: HookType, path: str, package: str
) -> t.Tuple[CodeType, CoverageLines]:
    # Enable local line events for the code object
    sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, sys.monitoring.events.LINE)  # noqa

    # Collect all the line numbers in the code object
    linestarts = dict(dis.findlinestarts(code))

    lines = CoverageLines()
    import_names: t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str, ...]]]] = {}

    # The previous two arguments are kept in order to track the depth of the IMPORT_NAME
    # For example, from ...package import module
    current_arg: int = 0
    previous_arg: int = 0
    _previous_previous_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

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
                if line is not None:
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

    # Recursively instrument nested code objects
    for nested_code in (_ for _ in code.co_consts if isinstance(_, CodeType)):
        _, nested_lines = instrument_all_lines(nested_code, hook, path, package)
        lines.update(nested_lines)

    # Register the hook and argument for the code object
    _CODE_HOOKS[code] = (hook, path, import_names)

    # Special case for empty modules (eg: __init__.py ):
    # Make sure line 0 is marked as executable, and add package dependency
    if not lines and code.co_name == "<module>" and code.co_code == EMPTY_MODULE_BYTES:
        lines.add(0)
        if package is not None:
            import_names[0] = (package, ("",))

    return code, lines
