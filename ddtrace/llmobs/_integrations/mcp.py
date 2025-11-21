from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.pin import Pin
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


log = get_logger(__name__)

MCP_SPAN_TYPE = "_ml_obs.mcp_span_type"

SERVER_TOOL_CALL_OPERATION_NAME = "server_tool_call"
CLIENT_TOOL_CALL_OPERATION_NAME = "client_tool_call"


def _find_client_session_root(span: Optional[Span]) -> Optional[Span]:
    """
    Find the root span of a client session.
    Note that this will not work in distributed tracing, but since
    all client operations should happen in the same service or process,
    this should mostly be safe.
    """
    while span is not None:
        if span._get_ctx_item(MCP_SPAN_TYPE) == "client_session":
            return span
        span = span._parent
    return None


def _set_or_update_tags(span: Span, tags: Dict[str, str]) -> None:
    existing_tags: Optional[Dict[str, str]] = span._get_ctx_item(TAGS)
    if existing_tags is not None:
        existing_tags.update(tags)
    else:
        span._set_ctx_item(TAGS, tags)


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs) -> Span:
        span = super().trace(pin, operation_id, submit_to_llmobs, **kwargs)

        mcp_span_type = kwargs.get("type", None)
        if mcp_span_type:
            span._set_ctx_item(MCP_SPAN_TYPE, mcp_span_type)

        return span

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
        if operation == CLIENT_TOOL_CALL_OPERATION_NAME:
            self._llmobs_set_tags_client(span, args, kwargs, response)
        elif operation == SERVER_TOOL_CALL_OPERATION_NAME:
            self._llmobs_set_tags_server(span, args, kwargs, response)
        elif operation == "initialize":
            self._llmobs_set_tags_initialize(span, args, kwargs, response)
        elif operation == "list_tools":
            self._llmobs_set_tags_list_tools(span, args, kwargs, response)
        elif operation == "session":
            self._llmobs_set_tags_session(span, args, kwargs, response)

    def _llmobs_set_tags_client(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any) -> None:
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        tool_name = args[0] if len(args) > 0 else kwargs.get("name", "unknown_tool")
        span_name = "MCP Client Tool Call: {}".format(tool_name)

        span._set_ctx_items(
            {
                SPAN_KIND: "tool",
                NAME: span_name,
                INPUT_VALUE: tool_arguments,
            }
        )

        client_session_root = _find_client_session_root(span)
        if client_session_root:
            client_session_root_tags = client_session_root._get_ctx_item(TAGS) or {}
            _set_or_update_tags(
                span,
                {
                    "mcp_server_name": client_session_root_tags.get("mcp_server_name", ""),
                },
            )

        _set_or_update_tags(span, {"mcp_tool_kind": "client"})

        if response is None:
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
                SPAN_KIND: "tool",
                NAME: span_name,
                INPUT_VALUE: tool_arguments,
            }
        )

        _set_or_update_tags(span, {"mcp_tool_kind": "server"})

        if span.error or response is None:
            return

        # As of mcp 1.10.0, the response object is list of `mcp.types.TextContent` objects since `run_tool` is called
        # with convert_result=True. Before this, the response was the raw tool result.
        if isinstance(response, list) and len(response) > 0 and _get_attr(response[0], "type", None) == "text":
            output_value = [self._parse_mcp_text_content(item) for item in response]
        else:
            output_value = safe_json(response)

        span._set_ctx_item(OUTPUT_VALUE, output_value)

    def _llmobs_set_tags_initialize(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any) -> None:
        span._set_ctx_items(
            {
                NAME: "MCP Client Initialize",
                SPAN_KIND: "task",
                OUTPUT_VALUE: safe_json(response),
            }
        )

        server_info = getattr(response, "serverInfo", None)
        if not server_info:
            return

        client_session_root = _find_client_session_root(span)
        if client_session_root:
            _set_or_update_tags(
                client_session_root,
                {
                    "mcp_server_name": getattr(server_info, "name", ""),
                    "mcp_server_version": getattr(server_info, "version", ""),
                    "mcp_server_title": getattr(server_info, "title", ""),
                },
            )

    def _llmobs_set_tags_list_tools(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any) -> None:
        cursor = get_argument_value(args, kwargs, 0, "cursor", optional=True)

        span._set_ctx_items(
            {
                NAME: "MCP Client List Tools",
                SPAN_KIND: "task",
                INPUT_VALUE: safe_json({"cursor": cursor}),
                OUTPUT_VALUE: safe_json(response),
            }
        )

    def _llmobs_set_tags_session(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any) -> None:
        read_stream = kwargs.get("read_stream", None)
        write_stream = kwargs.get("write_stream", None)

        span._set_ctx_items(
            {
                NAME: "MCP Client Session",
                SPAN_KIND: "workflow",
                INPUT_VALUE: safe_json({"read_stream": read_stream, "write_stream": write_stream}),
            }
        )
