from typing import Any
from typing import Optional

from ddtrace.contrib.internal.mcp._constants import CLIENT_TOOL_CALL_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import DD_TRACE_CONTEXT_META_KEY
from ddtrace.contrib.internal.mcp._constants import SERVER_MIDDLEWARE_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_REQUEST_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import SERVER_TOOL_CALL_OPERATION_NAME
from ddtrace.contrib.internal.mcp._constants import TELEMETRY_KEY
from ddtrace.contrib.internal.mcp._tool_result import is_tool_error
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


log = get_logger(__name__)

MCP_SPAN_TYPE = "_ml_obs.mcp_span_type"

MCP_SESSION_ID_HEADER = "mcp-session-id"
MCP_RESERVED_META_KEY_PREFIX = "io.modelcontextprotocol/"
# Client identity is self-reported and repeated on every tool call span in a session
MAX_CLIENT_INFO_TAG_LENGTH = 256


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


def get_client_info(request_params: Any, session: Any) -> Any:
    """mcp<2 uses camelCase field names, mcp>=2 uses snake_case."""
    client_info = _get_attr(request_params, "clientInfo", None) or _get_attr(request_params, "client_info", None)
    if client_info:
        return client_info
    client_params = _get_attr(session, "client_params", None)
    return _get_attr(client_params, "clientInfo", None) or _get_attr(client_params, "client_info", None)


def get_client_info_tags(client_info: Any) -> dict[str, str]:
    tags = {}
    for field in ("name", "title", "version"):
        value = _get_attr(client_info, field, None)
        if value:
            tags[f"mcp_client_{field}"] = str(value)[:MAX_CLIENT_INFO_TAG_LENGTH]
    return tags


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def trace(self, operation_id: str, submit_to_llmobs: bool = False, **kwargs) -> Span:
        span = super().trace(operation_id, submit_to_llmobs, **kwargs)

        mcp_span_type = kwargs.get("type", None)
        if mcp_span_type:
            span._set_ctx_item(MCP_SPAN_TYPE, mcp_span_type)

        return span

    # Inject intent capture properties into inputSchemas on the response
    def inject_tools_list_response(self, response: Any) -> None:
        if not self.llmobs_enabled:
            return

        for tool in _get_attr(response, "tools", None) or []:
            input_schema = _get_attr(tool, "inputSchema", None)
            if not isinstance(input_schema, dict):
                continue
            if not input_schema.get("type"):
                input_schema["type"] = "object"
            if "properties" not in input_schema:
                input_schema["properties"] = {}
            if "required" not in input_schema:
                input_schema["required"] = []

            input_schema["properties"][TELEMETRY_KEY] = dd_trace_input_schema()
            # The intent is passed as telemetry.intent, so telemetry is the top-level requirement
            if TELEMETRY_KEY not in input_schema["required"]:
                input_schema["required"].append(TELEMETRY_KEY)

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
        elif operation == SERVER_MIDDLEWARE_OPERATION_NAME:
            self._llmobs_set_tags_server_middleware(span, args, kwargs, response)
        elif operation == "list_tools":
            self._llmobs_set_tags_list_tools(span, args, kwargs, response)
        elif operation == "session":
            self._llmobs_set_tags_session(span, args, kwargs, response)

    def _llmobs_set_tags_client(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        tool_name = args[0] if len(args) > 0 else kwargs.get("name", "unknown_tool")
        span_name = f"MCP Client Tool Call: {tool_name}"

        tags: dict[str, str] = {"mcp_tool_kind": "client"}
        client_session_root = _find_client_session_root(span)
        if client_session_root:
            client_session_root_tags = get_llmobs_tags(client_session_root) or {}
            tags["mcp_server_name"] = client_session_root_tags.get("mcp_server_name", "")

        _annotate_llmobs_span_data(span, kind="tool", name=span_name, input_value=tool_arguments, tags=tags)

        if response is None:
            return

        # Tool response is `mcp.types.CallToolResult` type
        content = _get_attr(response, "content", [])
        is_error = is_tool_error(response)
        processed_content = []
        if content and hasattr(content, "__iter__"):
            processed_content = [
                self._parse_mcp_text_content(item) for item in content if _get_attr(item, "type", None) == "text"
            ]
        _annotate_llmobs_span_data(span, output_value={"content": processed_content, "isError": is_error})

    def _llmobs_set_tags_initialize(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
        _annotate_llmobs_span_data(span, name="MCP Client Initialize", kind="task", output_value=safe_json(response))
        self.set_client_session_server_info(
            span, _get_attr(response, "serverInfo", None) or _get_attr(response, "server_info", None)
        )

    def set_client_session_server_info(self, span: Optional[Span], server_info: Any) -> None:
        """Tag the enclosing client session span with the server's self-reported identity."""
        if not self.llmobs_enabled or not server_info:
            return

        client_session_root = _find_client_session_root(span)
        if client_session_root:
            _annotate_llmobs_span_data(
                client_session_root,
                tags={
                    "mcp_server_name": getattr(server_info, "name", ""),
                    "mcp_server_version": getattr(server_info, "version", ""),
                    "mcp_server_title": getattr(server_info, "title", ""),
                },
            )

    def _set_initialize_request_overrides(self, span: Span, request: Any) -> None:
        """Update span for initialize request specific tags"""
        request_params = _get_attr(request, "params", None)
        client_info = _get_attr(request_params, "clientInfo", None)
        client_name = _get_attr(client_info, "name", None)
        client_version = _get_attr(client_info, "version", None)
        if client_name and client_version:
            _annotate_llmobs_span_data(
                span,
                tags={
                    "client_name": str(client_name),
                    "client_version": f"{client_name}_{client_version}",
                },
            )

    def _set_call_tool_request_overrides(self, span: Span, request: Any, response: Any) -> None:
        """Update span for call tool-specific tags, span name, and span type"""
        override_tags = {}
        params = _get_attr(request, "params", None)
        tool_name = "unknown_tool"

        if params:
            tool_name = str(_get_attr(params, "name", tool_name))
            override_tags["mcp_tool"] = tool_name
            override_tags["mcp_tool_kind"] = "server"

        _annotate_llmobs_span_data(span, name=tool_name, kind="tool", tags=override_tags)

    def process_telemetry_argument(self, span: Span, request: Any) -> None:
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
                _annotate_llmobs_span_data(span, intent=intent)

            # The argument is removed before recording the input and calling the tool
            del arguments[TELEMETRY_KEY]

    def _set_server_request_tags(
        self, span: Span, request: Any, input_obj: Any, response: Any, session: Any, session_id: Any
    ) -> None:
        method = str(_get_attr(request, "method", "unknown"))
        tags = {"mcp_method": method}
        if session_id:
            tags["mcp_session_id"] = str(session_id)
        tags.update(get_client_info_tags(get_client_info(_get_attr(request, "params", None), session)))

        # Set defaults. Type-specific methods below may override.
        _annotate_llmobs_span_data(
            span, kind="task", input_value=safe_json(input_obj), output_value=safe_json(response), tags=tags
        )

        if method == "initialize":
            self._set_initialize_request_overrides(span, request)
        elif method == "tools/call":
            self._set_call_tool_request_overrides(span, request, response)

    def _llmobs_set_tags_request_responder_respond(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any
    ) -> None:
        responder = get_argument_value(args, kwargs, 0, "request_responder", optional=True)
        response_value = get_argument_value(args, kwargs, 0, "response", optional=True)

        request = getattr(responder, "request", None)
        request_root = getattr(request, "root", None)
        response_root = getattr(response_value, "root", response_value)

        # Session ID from streamable HTTP transport
        message_metadata = _get_attr(responder, "message_metadata", None)
        http_request = message_metadata and _get_attr(message_metadata, "request_context", None)
        session_id = http_request and getattr(http_request, "headers", {}).get(MCP_SESSION_ID_HEADER)

        # Exclude tracing context metadata from the recorded input
        if request_root and hasattr(request_root, "model_dump"):
            input_obj = request_root.model_dump(exclude={"params": {"meta": DD_TRACE_CONTEXT_META_KEY}})
        else:
            input_obj = request_root

        self._set_server_request_tags(
            span, request_root, input_obj, response_root, _get_attr(responder, "_session", None), session_id
        )

    def _llmobs_set_tags_server_middleware(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any
    ) -> None:
        ctx = kwargs["ctx"]
        params = ctx.params or {}
        request = {"method": ctx.method, "params": params}

        meta = params.get("_meta")
        if isinstance(meta, dict):
            # Stateless requests repeat the client's identity and capabilities under reserved keys on every call
            meta = {
                k: v
                for k, v in meta.items()
                if k != DD_TRACE_CONTEXT_META_KEY and not k.startswith(MCP_RESERVED_META_KEY_PREFIX)
            }
            params = {**params, "_meta": meta} if meta else {k: v for k, v in params.items() if k != "_meta"}
        input_obj = {"method": ctx.method, "params": params, "jsonrpc": "2.0", "id": ctx.request_id}

        headers = _get_attr(ctx.request, "headers", None)
        session_id = headers.get(MCP_SESSION_ID_HEADER) if headers is not None else None

        self._set_server_request_tags(span, request, input_obj, response, ctx.session, session_id)

    def _llmobs_set_tags_list_tools(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
        cursor = get_argument_value(args, kwargs, 0, "cursor", optional=True)

        _annotate_llmobs_span_data(
            span,
            name="MCP Client list Tools",
            kind="task",
            input_value=safe_json({"cursor": cursor}),
            output_value=safe_json(response),
        )

    def _llmobs_set_tags_session(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Any) -> None:
        read_stream = kwargs.get("read_stream", None)
        write_stream = kwargs.get("write_stream", None)

        _annotate_llmobs_span_data(
            span,
            name="MCP Client Session",
            kind="workflow",
            input_value=safe_json({"read_stream": read_stream, "write_stream": write_stream}),
        )
