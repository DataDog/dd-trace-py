import sys
from warnings import warn


TOOL_NAMES = {
    sys.monitoring.DEBUGGER_ID: "datadog-debugger",
}


def enable_tool(tool_id):
    if tool_id not in TOOL_NAMES:
        msg = f"Unsupported tool id {tool_id}"
        raise ValueError(msg)

    tool_name = TOOL_NAMES[tool_id]
    current_tool = sys.monitoring.get_tool(sys.monitoring.DEBUGGER_ID)
    if current_tool is not None:
        if current_tool == tool_name:
            return

        # The tool is already in use but it comes from a different source. We
        # override it to ensure that our tool is the one that is active, as per
        # the user's request.
        msg = f"Tool already in use by {current_tool}. Overriding with {tool_name}"
        warn(msg, RuntimeWarning)
        sys.monitoring.free_tool_id(sys.monitoring.DEBUGGER_ID)

    sys.monitoring.use_tool_id(sys.monitoring.DEBUGGER_ID, tool_name)
