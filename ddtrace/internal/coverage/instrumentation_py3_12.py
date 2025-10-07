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

_CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str, t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str]]]]]] = {}


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    coverage_tool = sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID)
    if coverage_tool is not None and coverage_tool != "datadog":
        log.debug("Coverage tool '%s' already registered, not gathering coverage", coverage_tool)
        return code, CoverageLines()

    if coverage_tool is None:
        log.debug("Registering code coverage tool")
        _register_monitoring()

    return _instrument_all_lines_with_monitoring(code, hook, path, package)


def _line_event_handler(code: CodeType, line: int) -> t.Any:
    hook, path, import_names = _CODE_HOOKS[code]
    import_name = import_names.get(line, None)
    return hook((line, path, import_name))


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
    #
    # For lightweight coverage, we enable monitoring for:
    # 1. The first line (to track module loading)
    # 2. All lines with IMPORT_NAME/IMPORT_FROM (to track import dependencies)
    line_starts_list = list(dis.findlinestarts(code))
    if not line_starts_list:
        # No line starts, return empty coverage
        return code, CoverageLines()

    line_starts_dict = dict(line_starts_list)
    first_line_start = min(o for o, _ in line_starts_list)

    # Find all line start offsets that contain IMPORT_NAME or IMPORT_FROM opcodes
    import_line_offsets = set()
    current_line_offset = None

    for i in range(0, len(code.co_code), 2):
        # Update current line if we hit a line start
        if i in line_starts_dict:
            current_line_offset = i

        # Check if this opcode is an import
        opcode = code.co_code[i]
        if opcode in (IMPORT_NAME, IMPORT_FROM) and current_line_offset is not None:
            import_line_offsets.add(current_line_offset)

    # Combine first offset with import line offsets
    offsets_to_monitor = {first_line_start} | import_line_offsets
    linestarts = {offset: line_starts_dict[offset] for offset in offsets_to_monitor}

    import_names: t.Dict[int, t.Tuple[str, t.Optional[t.Tuple[str, ...]]]] = {}

    # Track all executable lines for coverage reporting (not just the monitored lines)
    all_executable_lines = CoverageLines()
    for _, line in line_starts_dict.items():
        all_executable_lines.add(line)

    # The previous two arguments are kept in order to track the depth of the IMPORT_NAME
    # For example, from ...package import module
    current_arg: int = 0
    previous_arg: int = 0
    _previous_previous_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    line = 0

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

                # Make sure that the current module is marked as depending on its own package by instrumenting the
                # first executable line
                if code.co_name == "<module>" and line == linestarts[first_line_start] and package is not None:
                    import_names[line] = (package, ("",))

            if opcode is EXTENDED_ARG:
                ext.append(arg)
                continue
            else:
                _previous_previous_arg = previous_arg
                previous_arg = current_arg
                current_arg = int.from_bytes([*ext, arg], "big", signed=False)
                ext.clear()

            if opcode == IMPORT_NAME:
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
            if opcode == IMPORT_FROM:
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
        all_executable_lines.update(nested_lines)

    # Register the hook and argument for the code object
    _CODE_HOOKS[code] = (hook, path, import_names)

    # Special case for empty modules (eg: __init__.py ):
    # Make sure line 0 is marked as executable, and add package dependency
    if not all_executable_lines and code.co_name == "<module>" and code.co_code == EMPTY_MODULE_BYTES:
        all_executable_lines.add(0)
        if package is not None:
            import_names[0] = (package, ("",))

    return code, all_executable_lines
