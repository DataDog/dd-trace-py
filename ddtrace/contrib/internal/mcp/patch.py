import sys
from typing import Dict

import mcp

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.trace import Pin


log = get_logger(__name__)

config._add("mcp", {})


def get_version() -> str:
    # mcp doesn't seem to have __version__ or .version attribute
    from importlib.metadata import version

    return version("mcp")


def _supported_versions() -> Dict[str, str]:
    return {"mcp": ">=1.0.0"}


@with_traced_module
def traced_call_tool(mcp, pin, func, instance, args, kwargs):
    integration = mcp._datadog_integration
    print(f"args: {args}")
    print("INSIDE traced_call_tool")
    span = integration.trace(
        pin,
        "MCP Tool Call",
        operation="call_tool",
        submit_to_llmobs=True,
    )

    # Inject distributed tracing headers into tool arguments before the call
    if integration.llmobs_enabled:
        from ddtrace.llmobs import LLMObs

        headers = LLMObs.inject_distributed_headers({})
        if headers and len(args) > 1:
            # Modify the arguments parameter (second argument) to include headers
            tool_arguments = args[1] if args[1] is not None else {}
            tool_arguments = dict(tool_arguments) if tool_arguments else {}
            tool_arguments["_dd_trace_headers"] = headers
            # Update args tuple with modified arguments
            args = (args[0], tool_arguments) + args[2:]

    result = None
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="call_tool")
        span.finish()

    return result


@with_traced_module
async def traced_tool_run(mcp, pin, func, instance, args, kwargs):
    integration = mcp._datadog_integration

    # Extract and activate distributed tracing headers BEFORE creating the span
    headers = None
    if integration.llmobs_enabled and args and len(args) > 0:
        # For FastMCP Tool.run(), arguments is the first parameter
        tool_arguments = args[0] if args[0] is not None else {}
        if isinstance(tool_arguments, dict) and "_dd_trace_headers" in tool_arguments:
            headers = tool_arguments.pop("_dd_trace_headers")
            if headers:
                from ddtrace.llmobs import LLMObs

                LLMObs.activate_distributed_headers(headers)
            # Update args tuple with cleaned arguments
            args = (tool_arguments,) + args[1:]

    span = integration.trace(
        pin,
        "MCP Tool Execute",
        operation="execute_tool",
        submit_to_llmobs=True,
    )

    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["instance"] = instance
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=result, operation="execute_tool", headers=headers
        )
        span.finish()

    return result


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = True

    Pin().onto(mcp)

    integration = MCPIntegration(integration_config=config.mcp)
    mcp._datadog_integration = integration

    from mcp.client.session import ClientSession

    wrap(ClientSession, "call_tool", traced_call_tool(mcp))

    from mcp.server.fastmcp.tools.base import Tool

    wrap(Tool, "run", traced_tool_run(mcp))


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    from mcp.client.session import ClientSession

    unwrap(ClientSession, "call_tool")

    from mcp.server.fastmcp.tools.base import Tool

    unwrap(Tool, "run")

    delattr(mcp, "_datadog_integration")
