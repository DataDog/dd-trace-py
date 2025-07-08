import sys
from typing import Dict

import mcp

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.trace import Pin


config._add("mcp", {})


def get_version() -> str:
    from importlib.metadata import version

    return version("mcp")


def _supported_versions() -> Dict[str, str]:
    return {"mcp": ">=1.0.0"}


@with_traced_module
async def traced_call_tool(mcp, pin, func, instance, args, kwargs):
    integration = mcp._datadog_integration
    span = integration.trace(pin, "call_tool", span_name="MCP Tool Call", submit_to_llmobs=True)

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

    try:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="call_tool")
        return result
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


@with_traced_module
async def traced_tool_run(mcp, pin, func, instance, args, kwargs):
    integration = mcp._datadog_integration

    # Extract and activate distributed tracing headers BEFORE creating the span
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

    span = integration.trace(pin, "execute_tool", span_name="MCP Tool Execute", submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="execute_tool")
        return result
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


def patch():
    if getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = True
    Pin().onto(mcp)
    mcp._datadog_integration = MCPIntegration(integration_config=config.mcp)

    from mcp.client.session import ClientSession
    from mcp.server.fastmcp.tools.base import Tool

    wrap(ClientSession, "call_tool", traced_call_tool(mcp))
    wrap(Tool, "run", traced_tool_run(mcp))


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    from mcp.client.session import ClientSession
    from mcp.server.fastmcp.tools.base import Tool

    unwrap(ClientSession, "call_tool")
    unwrap(Tool, "run")

    delattr(mcp, "_datadog_integration")
