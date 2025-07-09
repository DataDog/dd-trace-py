from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span


log = get_logger(__name__)


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def get_span_name(self, operation: str, args: List[Any], kwargs: Dict[str, Any]) -> str:
        if operation == "call_tool":
            tool_name = args[0] if len(args) > 0 else kwargs.get("name", "unknown_tool")
            return f"MCP Client Tool Call: {tool_name}"
        elif operation == "execute_tool":
            tool_name = args[0] if len(args) > 0 else "unknown_tool"
            return f"MCP Server Tool Execute: {tool_name}"
        return f"MCP {operation}"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span._set_ctx_item(SPAN_KIND, "tool")

        if operation == "call_tool":
            self._llmobs_set_tags_client_call(span, args, kwargs, response)
        elif operation == "execute_tool":
            self._llmobs_set_tags_server_execute(span, args, kwargs, response)

    def _llmobs_set_tags_client_call(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any]
    ):
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        span._set_ctx_items(
            {
                NAME: self.get_span_name("call_tool", args, kwargs),
                METADATA: {"mcp.operation": "call_tool"},
                INPUT_VALUE: tool_arguments,
            }
        )
        if span.error:
            return
        output_value = None
        if response:
            if hasattr(response, "content"):
                output_value = getattr(response, "content", None)
            elif hasattr(response, "result"):
                output_value = getattr(response, "result", None)
            else:
                output_value = str(response)
        span._set_ctx_item(OUTPUT_VALUE, output_value)

    def _llmobs_set_tags_server_execute(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any],
    ):
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        span._set_ctx_items(
            {
                NAME: self.get_span_name("execute_tool", args, kwargs),
                METADATA: {"mcp.operation": "execute_tool"},
                INPUT_VALUE: tool_arguments,
            }
        )

        if span.error:
            return

        output_value = response
        if hasattr(response, "content"):
            output_value = getattr(response, "content", None)
        elif hasattr(response, "result"):
            output_value = getattr(response, "result", None)

        span._set_ctx_item(OUTPUT_VALUE, output_value)
