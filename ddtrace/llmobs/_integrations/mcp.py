from typing import TYPE_CHECKING
from typing import Any
from typing import Optional


if TYPE_CHECKING:
    from mcp.types import CallToolRequest
    from mcp.types import CallToolResult
    from mcp.types import InitializeRequest
    from mcp.types import ListToolsResult

from ddtrace._trace.pin import Pin
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import MCP_TOOL_CALL_INTENT
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
SERVER_REQUEST_OPERATION_NAME = "server_request"
# This operation is handled the same as server_request but has a different name for legacy reasons
SERVER_TOOL_CALL_OPERATION_NAME = "server_tool_call"


TELEMETRY_KEY = "telemetry"
INTENT_KEY = "intent"
INTENT_PROMPT = """Briefly describe the wider context task, and why this tool was chosen.
 Omit argument values, PII/secrets. Use English.
 """


def dd_trace_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            INTENT_KEY: {
                "type": "string",
                "description": INTENT_PROMPT,
            },
        },
        "required": [INTENT_KEY],
    }


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


def _set_or_update_tags(span: Span, tags: dict[str, str]) -> None:
    existing_tags: Optional[dict[str, str]] = span._get_ctx_item(TAGS)
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

    # Inject intent capture properties into inputSchemas on the response
    def inject_tools_list_response(self, response: "ListToolsResult") -> None:
        if not self.llmobs_enabled:
            return

        for tool in response.tools:
            input_schema = getattr(tool, "inputSchema", None)
            if not isinstance(input_schema, dict):
                continue
            if not input_schema.get("type"):
                input_schema["type"] = "object"
            if "properties" not in input_schema:
                input_schema["properties"] = {}
            if "required" not in input_schema:
                input_schema["required"] = []

            input_schema["properties"][TELEMETRY_KEY] = dd_trace_input_schema()
            input_schema["required"].append(INTENT_KEY)

    def _parse_mcp_text_content(self, item: Any) -> dict[str, Any]:
        """Parse MCP TextContent fields, extracting only non-None values."""
        annotations = _get_attr(item, "annotations", None)
        annotations_dict = {}
        if annotations and hasattr(annotations, "model_dump"):
            annotations_dict = annotations.model_dump()

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
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        if operation == CLIENT_TOOL_CALL_OPERATION_NAME:
            self._llmobs_set_tags_client(span, args, kwargs, response)
        elif operation == "initialize":
            self._llmobs_set_tags_initialize(span, args, kwargs, response)
        elif operation == SERVER_REQUEST_OPERATION_NAME or operation == SERVER_TOOL_CALL_OPERATION_NAME:
            self._llmobs_set_tags_request_responder_respond(span, args, kwargs, response)
        elif operation == "list_tools":
            self._llmobs_set_tags_list_tools(span, args, kwargs, response)
        elif operation == "session":
            self._llmobs_set_tags_session(span, args, kwargs, response)

    def _llmobs_set_tags_client(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
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
                self._parse_mcp_text_content(item) for item in content if _get_attr(item, "type", None) == "text"
            ]
        output_value = {"content": processed_content, "isError": is_error}
        span._set_ctx_item(OUTPUT_VALUE, output_value)

    def _llmobs_set_tags_initialize(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
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

    def _set_initialize_request_overrides(self, span: Span, request: "InitializeRequest") -> None:
        """Update span for initialize request specific tags"""
        request_params = _get_attr(request, "params", None)
        client_info = _get_attr(request_params, "clientInfo", None)
        client_name = _get_attr(client_info, "name", None)
        client_version = _get_attr(client_info, "version", None)
        if client_name and client_version:
            _set_or_update_tags(
                span,
                {
                    "client_name": str(client_name),
                    "client_version": f"{client_name}_{client_version}",
                },
            )

    def _set_call_tool_request_overrides(
        self, span: Span, request: "CallToolRequest", response: Optional["CallToolResult"]
    ) -> None:
        """Update span for call tool-specific tags, span name, and span type"""
        override_tags = {}
        params = _get_attr(request, "params", None)
        tool_name = "unknown_tool"

        if params:
            tool_name = str(_get_attr(params, "name", tool_name))
            override_tags["mcp_tool"] = tool_name
            override_tags["mcp_tool_kind"] = "server"

        is_error = _get_attr(response, "isError", False) if response else False
        if is_error:
            span.error = 1
            span.set_tag(ERROR_TYPE, "ToolError")
            span.set_tag(ERROR_MSG, "tool resulted in an error")
        _set_or_update_tags(span, override_tags)
        span._set_ctx_items({NAME: tool_name, SPAN_KIND: "tool"})

    def process_telemetry_argument(self, span: Span, request: "CallToolRequest") -> None:
        """Process and remove telemetry argument from requests
        This is called before the tool is called or the input is recorded
        """
        if not self.llmobs_enabled:
            return

        params = _get_attr(request, "params", None)
        arguments = _get_attr(params, "arguments", None)
        telemetry = _get_attr(arguments, TELEMETRY_KEY, None)
        if isinstance(arguments, dict) and telemetry:
            intent = _get_attr(telemetry, INTENT_KEY, None)
            if intent:
                span._set_ctx_item(MCP_TOOL_CALL_INTENT, intent)

            # The argument is removed before recording the input and calling the tool
            del arguments[TELEMETRY_KEY]

    def _llmobs_set_tags_request_responder_respond(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any
    ) -> None:
        try:
            from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
            from mcp.types import CallToolRequest
            from mcp.types import InitializeRequest
        except ImportError:
            InitializeRequest = None
            MCP_SESSION_ID_HEADER = None
            CallToolRequest = None

        responder = get_argument_value(args, kwargs, 0, "request_responder", optional=True)
        response_value = get_argument_value(args, kwargs, 0, "response", optional=True)

        request = getattr(responder, "request", None)
        request_root = getattr(request, "root", None)
        response_root = getattr(response_value, "root", response_value)

        request_method = str(_get_attr(request_root, "method", "unknown"))
        common_tags = {"mcp_method": request_method}

        # Session ID from streamable HTTP transport
        message_metadata = _get_attr(responder, "message_metadata", None)
        http_request = message_metadata and _get_attr(message_metadata, "request_context", None)
        maybe_session_id = (
            http_request and getattr(http_request, "headers", {}).get(MCP_SESSION_ID_HEADER)
            if MCP_SESSION_ID_HEADER
            else None
        )
        if maybe_session_id:
            common_tags["mcp_session_id"] = str(maybe_session_id)

        # Exclude tracing context metadata from the recorded input
        if request_root and hasattr(request_root, "model_dump"):
            input_obj = request_root.model_dump(exclude={"params": {"meta": "_dd_trace_context"}})
        else:
            input_obj = request_root

        # Set defaults. Type-specific methods below may override.
        span._set_ctx_items(
            {
                SPAN_KIND: "task",
                INPUT_VALUE: safe_json(input_obj),
                OUTPUT_VALUE: safe_json(response_root),
                TAGS: common_tags,
            }
        )

        if InitializeRequest and request_root and isinstance(request_root, InitializeRequest):
            self._set_initialize_request_overrides(span, request_root)
        if CallToolRequest and request_root and isinstance(request_root, CallToolRequest):
            self._set_call_tool_request_overrides(span, request_root, response_root)

    def _llmobs_set_tags_list_tools(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
        cursor = get_argument_value(args, kwargs, 0, "cursor", optional=True)

        span._set_ctx_items(
            {
                NAME: "MCP Client list Tools",
                SPAN_KIND: "task",
                INPUT_VALUE: safe_json({"cursor": cursor}),
                OUTPUT_VALUE: safe_json(response),
            }
        )

    def _llmobs_set_tags_session(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
        read_stream = kwargs.get("read_stream", None)
        write_stream = kwargs.get("write_stream", None)

        span._set_ctx_items(
            {
                NAME: "MCP Client Session",
                SPAN_KIND: "workflow",
                INPUT_VALUE: safe_json({"read_stream": read_stream, "write_stream": write_stream}),
            }
        )
