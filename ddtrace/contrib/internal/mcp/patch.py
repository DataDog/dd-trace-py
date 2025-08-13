import os
import sys
from typing import Any
from typing import Dict
from typing import Optional

import mcp

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._integrations.mcp import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.llmobs._integrations.mcp import MCPIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Pin


log = get_logger(__name__)

config._add(
    "mcp",
    {
        "distributed_tracing": asbool(os.getenv("DD_MCP_DISTRIBUTED_TRACING", default=True)),
    },
)


def get_version() -> str:
    from importlib.metadata import version

    return version("mcp")


def _supported_versions() -> Dict[str, str]:
    return {"mcp": ">=1.10.0"}


def _set_distributed_headers_into_mcp_request(pin, request):
    """Inject distributed tracing headers into MCP request metadata."""
    span = pin.tracer.current_span()
    if span is None:
        return request

    headers = {}
    HTTPPropagator.inject(span.context, headers)
    if not headers:
        return request
    if _get_attr(request, "root", None) is None:
        return request

    try:
        request_params = _get_attr(request.root, "params", None)
        if not request_params:
            return request

        # Use the `_meta` field to store tracing headers. It is accessed via a public
        # `meta` attribute on the request params. This field is reserved for server/clients
        # to attach additional metadata to a request. For more information, see:
        # https://modelcontextprotocol.io/specification/2025-06-18/basic#meta
        existing_meta = _get_attr(request_params, "meta", None)
        meta_dict = existing_meta.model_dump() if existing_meta else {}

        meta_dict["_dd_trace_context"] = headers
        params_dict = request_params.model_dump(by_alias=True)
        params_dict["_meta"] = meta_dict

        new_params = type(request_params)(**params_dict)
        request_dict = request.root.model_dump()
        request_dict["params"] = new_params

        new_request_root = type(request.root)(**request_dict)
        return type(request)(new_request_root)
    except Exception:
        log.error("Error injecting distributed tracing headers into MCP request metadata", exc_info=True)
        return request


def _extract_distributed_headers_from_mcp_request(kwargs: Dict[str, Any]) -> Optional[Dict[str, str]]:
    if "context" not in kwargs:
        return
    context = kwargs.get("context")
    if not context or not _get_attr(context, "request_context", None):
        return
    request_context = _get_attr(context, "request_context", None)
    meta = _get_attr(request_context, "meta", None)
    if not meta:
        return
    headers = _get_attr(meta, "_dd_trace_context", None)
    if headers:
        return headers


@with_traced_module
def traced_send_request(mcp, pin, func, instance, args, kwargs):
    """Injects distributed tracing headers into MCP request metadata"""
    if not args or not config.mcp.distributed_tracing:
        return func(*args, **kwargs)
    request = args[0]
    modified_request = _set_distributed_headers_into_mcp_request(pin, request)
    return func(*((modified_request,) + args[1:]), **kwargs)


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
    if config.mcp.distributed_tracing:
        activate_distributed_headers(pin.tracer, config.mcp, _extract_distributed_headers_from_mcp_request(kwargs))

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
    from mcp.shared.session import BaseSession

    wrap(BaseSession, "send_request", traced_send_request(mcp))
    wrap(ClientSession, "call_tool", traced_call_tool(mcp))
    wrap(ToolManager, "call_tool", traced_tool_manager_call_tool(mcp))


def unpatch():
    if not getattr(mcp, "__datadog_patch", False):
        return

    mcp.__datadog_patch = False

    from mcp.client.session import ClientSession
    from mcp.server.fastmcp.tools.tool_manager import ToolManager
    from mcp.shared.session import BaseSession

    unwrap(BaseSession, "send_request")
    unwrap(ClientSession, "call_tool")
    unwrap(ToolManager, "call_tool")

    delattr(mcp, "_datadog_integration")
