from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


log = get_logger(__name__)

SERVER_TOOL_CALL_OPERATION_NAME = "server_tool_call"
CLIENT_TOOL_CALL_OPERATION_NAME = "client_tool_call"


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def _parse_mcp_text_content(self, item: Any) -> Dict[str, Any]:
        """Parse MCP TextContent fields, extracting only non-None values."""
        content_block = {
            "type": _get_attr(item, "type", "") or "",
            "annotations": annotations.model_dump()
            if (annotations := _get_attr(item, "annotations", None)) and hasattr(annotations, "model_dump")
            else {},
            "meta": _get_attr(item, "meta", {}) or {},
        }
        if content_block["type"] == "text":
            content_block["text"] = _get_attr(item, "text", "") or ""
        return content_block

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
        tool_name = args[0] if len(args) > 0 else kwargs.get("name", "unknown_tool")
        span_name = "MCP Client Tool Call: {}".format(tool_name)

        span._set_ctx_items(
            {
                NAME: span_name,
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
        tool_name = args[0] if len(args) > 0 else "unknown_tool"
        span_name = "MCP Server Tool Execute: {}".format(tool_name)

        span._set_ctx_items(
            {
                NAME: span_name,
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
