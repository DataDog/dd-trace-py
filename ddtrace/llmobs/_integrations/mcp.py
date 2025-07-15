from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Span


log = get_logger(__name__)

SERVER_TOOL_CALL_OPERATION_NAME = "server_tool_call"
CLIENT_TOOL_CALL_OPERATION_NAME = "client_tool_call"


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def get_span_name(self, operation: str, args: List[Any], kwargs: Dict[str, Any]) -> str:
        """Generate span name for MCP operations."""
        if operation == CLIENT_TOOL_CALL_OPERATION_NAME:
            tool_name = args[0] if len(args) > 0 else kwargs.get("name", "unknown_tool")
            return f"MCP Client Tool Call: {tool_name}"
        elif operation == SERVER_TOOL_CALL_OPERATION_NAME:
            tool_name = args[0] if len(args) > 0 else "unknown_tool"
            return f"MCP Server Tool Execute: {tool_name}"
        return f"MCP {operation}"

    def inject_distributed_headers(self, request):
        """Inject distributed tracing headers into MCP request metadata."""
        if not self.llmobs_enabled:
            return request

        span = LLMObs._instance.tracer.current_span()
        if span is None:
            return request

        headers = {}
        HTTPPropagator.inject(span.context, headers)
        if not headers:
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

            meta_dict["dd_trace_context"] = headers
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

    def extract_and_activate_distributed_headers(self, kwargs: Dict[str, Any]) -> None:
        """Extract distributed tracing headers from MCP request context and activate them."""
        if not self.llmobs_enabled or "context" not in kwargs:
            return

        context = kwargs.get("context")
        if not context or not hasattr(context, "request_context") or not context.request_context:
            return

        try:
            request_context = context.request_context
            if not hasattr(request_context, "meta") or not request_context.meta:
                return
            headers = _get_attr(request_context.meta, "dd_trace_context", None)
            if headers:
                context = HTTPPropagator.extract(headers)
                LLMObs._instance.tracer.context_provider.activate(context)
                LLMObs._activate_llmobs_distributed_context(headers, context)
        except Exception:
            log.error("Error extracting distributed tracing headers from MCP request context", exc_info=True)

    def _parse_mcp_text_content(self, item: Any) -> Dict[str, Any]:
        """Parse MCP TextContent fields, extracting only non-None values."""
        return {
            field_name: field_value
            for field_name in ["type", "text", "annotations", "meta"]
            if (field_value := _get_attr(item, field_name, None)) is not None
        }

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span._set_ctx_item(SPAN_KIND, "tool")

        if operation == CLIENT_TOOL_CALL_OPERATION_NAME:
            self._llmobs_set_tags_client(span, args, kwargs, response)
        elif operation == SERVER_TOOL_CALL_OPERATION_NAME:
            self._llmobs_set_tags_server(span, args, kwargs, response)

    def _llmobs_set_tags_client(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any) -> None:
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        span_name = self.get_span_name(CLIENT_TOOL_CALL_OPERATION_NAME, args, kwargs)

        span._set_ctx_items(
            {
                NAME: span_name,
                METADATA: {"mcp.operation": CLIENT_TOOL_CALL_OPERATION_NAME},
                INPUT_VALUE: tool_arguments,
            }
        )

        if span.error or response is None:
            return

        # Tool response is `mcp.types.CallToolResult` type
        content = _get_attr(response, "content", [])
        is_error = _get_attr(response, "isError", False)
        processed_content = [
            self._parse_mcp_text_content(item) for item in content if _get_attr(item, "type", None) == "text"
        ]
        output_value = {"content": processed_content, "isError": is_error}
        span._set_ctx_item(OUTPUT_VALUE, output_value)

    def _llmobs_set_tags_server(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any) -> None:
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        span_name = self.get_span_name(SERVER_TOOL_CALL_OPERATION_NAME, args, kwargs)

        span._set_ctx_items(
            {
                NAME: span_name,
                METADATA: {"mcp.operation": SERVER_TOOL_CALL_OPERATION_NAME},
                INPUT_VALUE: tool_arguments,
            }
        )

        if span.error or response is None:
            return

        # As of mcp 1.10.0, the response object is list of `mcp.types.TextContent` objects since `run_tool` is called
        # with convert_result=True. Before this, the response was the raw tool result.
        if isinstance(response, list) and len(response) > 0 and _get_attr(response[0], "type", None) == "text":
            output_value = [self._parse_mcp_text_content(item) for item in response]
        else:
            output_value = safe_json(response)

        span._set_ctx_item(OUTPUT_VALUE, output_value)
