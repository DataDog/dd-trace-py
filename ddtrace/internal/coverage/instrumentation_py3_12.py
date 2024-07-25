import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.injection import HookType


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec


# Register the coverage tool with the low-impact monitoring system
try:
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")  # noqa
except ValueError:
    # TODO: Another coverage tool is already in use. Either warn the user
    # or free the tool and register ours.
    def instrument_all_lines(code: CodeType, hook: HookType, path: str) -> t.Tuple[CodeType, t.Set[int]]:
        # No-op
        return code, set()

else:
    RESUME = dis.opmap["RESUME"]

    _CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str]] = {}

    def _line_event_handler(code: CodeType, line: int) -> t.Any:
        hook, path = _CODE_HOOKS[code]
        return hook((line, path))

    # Register the line callback
    sys.monitoring.register_callback(
        sys.monitoring.COVERAGE_ID, sys.monitoring.events.LINE, _line_event_handler
    )  # noqa

    def instrument_all_lines(code: CodeType, hook: HookType, path: str) -> t.Tuple[CodeType, t.Set[int]]:
        # Enable local line events for the code object
        sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, sys.monitoring.events.LINE)  # noqa

        # Collect all the line numbers in the code object
        lines = {line for o, line in dis.findlinestarts(code) if code.co_code[o] != RESUME}

        # Recursively instrument nested code objects
        for nested_code in (_ for _ in code.co_consts if isinstance(_, CodeType)):
            _, nested_lines = instrument_all_lines(nested_code, hook, path)
            lines.update(nested_lines)

        # Register the hook and argument for the code object
        _CODE_HOOKS[code] = (hook, path)

        return code, lines
