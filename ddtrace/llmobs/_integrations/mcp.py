from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.pin import Pin
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
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

CLIENT_TOOL_CALL_OPERATION_NAME = "client_tool_call"
REQUEST_RESPONDER_ENTER_OPERATION_NAME = "request_responder"
REQUEST_RESPONDER_RESPOND_OPERATION_NAME = "request_responder_respond"


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
        annotations = _get_attr(item, "annotations", None)
        annotations_dict = {}
        if annotations and hasattr(annotations, "model_dump"):
            annotations_dict = annotations.model_dump()  # type: ignore[union-attr]

        content_block = {
            "type": _get_attr(item, "type", "") or "",
            "annotations": annotations_dict,
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
        elif operation == "initialize":
            self._llmobs_set_tags_initialize(span, args, kwargs, response)
        elif operation == REQUEST_RESPONDER_RESPOND_OPERATION_NAME:
            self._llmobs_set_tags_request_responder_respond(span, args, kwargs, response)
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
        processed_content = []
        if content and hasattr(content, "__iter__"):
            processed_content = [
                self._parse_mcp_text_content(item)
                for item in content
                if _get_attr(item, "type", None) == "text"  # type: ignore[union-attr]
            ]
        output_value = {"content": processed_content, "isError": is_error}
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

    def _llmobs_set_tags_request_responder_respond(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any
    ) -> None:
        try:
            from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
            from mcp.types import CallToolRequest
            from mcp.types import CallToolResult
            from mcp.types import InitializeRequest
        except ImportError:
            InitializeRequest = None
            MCP_SESSION_ID_HEADER = None
            CallToolRequest = None
            CallToolResult = None

        responder = get_argument_value(args, kwargs, 0, "request_responder", optional=True)
        response_value = get_argument_value(args, kwargs, 0, "response", optional=True)

        request = getattr(responder, "request", None)
        request_root = getattr(request, "root", None)

        response_root = getattr(response_value, "root", response_value)

        request_method = str(_get_attr(request_root, "method", "unknown"))
        span_kind = "task"
        span_name = "mcp.{}".format(request_method)
        custom_tags = {
            "mcp_method": request_method,
        }

        if InitializeRequest and request_root and isinstance(request_root, InitializeRequest):
            request_params = _get_attr(request_root, "params", None)
            client_info = _get_attr(request_params, "clientInfo", None)
            client_name = _get_attr(client_info, "name", None)
            client_version = _get_attr(client_info, "version", None)
            if isinstance(client_name, str) and isinstance(client_version, str):
                custom_tags["client_name"] = client_name
                custom_tags["client_version"] = f"{client_name}_{client_version}"

        if CallToolRequest and request_root and isinstance(request_root, CallToolRequest):
            params = _get_attr(request_root, "params", None)
            if params:
                tool_name = str(_get_attr(params, "name", "unknown_tool"))
                custom_tags["mcp_tool"] = tool_name
                custom_tags["mcp_tool_kind"] = "server"
                span_name = tool_name
            span_kind = "tool"

        if CallToolResult and isinstance(response_root, CallToolResult) and response_root.isError:
            span.error = 1
            span.set_tag(ERROR_TYPE, "ToolError")
            span.set_tag(ERROR_MSG, "tool resulted in an error")

        # This only exists for existing sessions using the streamable HTTP transport
        message_metadata = _get_attr(responder, "message_metadata", None)
        http_request = message_metadata and _get_attr(message_metadata, "request_context", None)
        maybe_session_id = (
            http_request and getattr(http_request, "headers", {}).get(MCP_SESSION_ID_HEADER)
            if MCP_SESSION_ID_HEADER
            else None
        )

        if maybe_session_id:
            custom_tags["mcp_session_id"] = str(maybe_session_id)


        span._set_ctx_items(
            {NAME: span_name, SPAN_KIND: span_kind, INPUT_VALUE: safe_json(request_root), OUTPUT_VALUE: safe_json(response_root)}
        )
        if custom_tags:
            _set_or_update_tags(span, custom_tags)

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
