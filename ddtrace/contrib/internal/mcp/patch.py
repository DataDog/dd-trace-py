import sys
from typing import Dict

import mcp

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.trace import Pin


config._add("mcp", {})


def get_version() -> str:
    from importlib.metadata import version

    return version("mcp")


def _supported_versions() -> Dict[str, str]:
    return {"mcp": ">=1.10.0"}


@with_traced_module
async def traced_call_tool(mcp, pin, func, instance, args, kwargs):
    integration = mcp._datadog_integration

    span = integration.trace(pin, CLIENT_TOOL_CALL_OPERATION_NAME, submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=result, operation=CLIENT_TOOL_CALL_OPERATION_NAME
        )
        return result
    except Exception:
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=None, operation=CLIENT_TOOL_CALL_OPERATION_NAME
        )
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


@with_traced_module
async def traced_tool_manager_call_tool(mcp, pin, func, instance, args, kwargs):
    integration = mcp._datadog_integration

    span = integration.trace(pin, SERVER_TOOL_CALL_OPERATION_NAME, submit_to_llmobs=True)

    try:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=result, operation=SERVER_TOOL_CALL_OPERATION_NAME
        )
        return result
    except Exception:
        integration.llmobs_set_tags(
            span, args=args, kwargs=kwargs, response=None, operation=SERVER_TOOL_CALL_OPERATION_NAME
        )
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
    from mcp.server.fastmcp.tools.tool_manager import ToolManager

    wrap(ClientSession, "call_tool", traced_call_tool(mcp))
    wrap(ToolManager, "call_tool", traced_tool_manager_call_tool(mcp))


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    from mcp.client.session import ClientSession
    from mcp.server.fastmcp.tools.tool_manager import ToolManager

    unwrap(ClientSession, "call_tool")
    unwrap(ToolManager, "call_tool")

    delattr(mcp, "_datadog_integration")
