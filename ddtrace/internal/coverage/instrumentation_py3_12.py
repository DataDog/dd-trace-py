import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.injection import HookType


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
IMPORT_NAME = dis.opmap["IMPORT_NAME"]


# Register the coverage tool with the low-impact monitoring system
try:
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")  # noqa
except ValueError:
    # TODO: Another coverage tool is already in use. Either warn the user
    # or free the tool and register ours.
    def instrument_all_lines(
        code: CodeType, hook: HookType, path: str, package: t.Optional[str]
    ) -> t.Tuple[CodeType, t.Set[int]]:
        # No-op
        return code, set()

else:
    RESUME = dis.opmap["RESUME"]

    _CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str, t.Dict[int, str]]] = {}

    def _line_event_handler(code: CodeType, line: int) -> t.Any:
        hook, path, import_names = _CODE_HOOKS[code]
        import_name = import_names.get(line, None)
        return hook((line, path, import_name))

    # Register the line callback
    sys.monitoring.register_callback(
        sys.monitoring.COVERAGE_ID, sys.monitoring.events.LINE, _line_event_handler
    )  # noqa

    def instrument_all_lines(
        code: CodeType, hook: HookType, path: str, package: t.Optional[str]
    ) -> t.Tuple[CodeType, t.Set[int]]:
        # Enable local line events for the code object
        sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, sys.monitoring.events.LINE)  # noqa

        # Collect all the line numbers in the code object
        linestarts = dict(dis.findlinestarts(code))

        lines = set()
        import_names: t.Dict[int, str] = {}

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
                    lines.add(line)

                if opcode == IMPORT_NAME:
                    import_arg = int.from_bytes([*ext, arg], "big", signed=False)
                    import_name = code.co_names[import_arg]
                    if import_name.startswith(".") and package is not None:
                        import_name = f"{package}.{import_name}"
                    import_names[line] = import_name

                    # Accumulate extended args IMPORT_NAME arguments
                if opcode is EXTENDED_ARG:
                    ext.append(arg)
                else:
                    ext.clear()
        except StopIteration:
            pass

        # Recursively instrument nested code objects
        for nested_code in (_ for _ in code.co_consts if isinstance(_, CodeType)):
            _, nested_lines = instrument_all_lines(nested_code, hook, path, package)
            lines.update(nested_lines)

        # Register the hook and argument for the code object
        _CODE_HOOKS[code] = (hook, path, import_names)

        return code, lines
