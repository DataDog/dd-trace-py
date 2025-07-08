from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        if not self.llmobs_enabled:
            return
        try:
            self._llmobs_set_tags(span, args, kwargs, response, operation)
        except Exception:
            from ddtrace.internal.logger import get_logger

            log = get_logger(__name__)
            log.error("Error extracting LLMObs fields for span %s, likely due to malformed data", span, exc_info=True)

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
        tool_name = get_argument_value(args, kwargs, 0, "name", optional=True) or "unknown_tool"
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}

        span._set_ctx_items(
            {
                NAME: f"MCP Client Tool Call: {tool_name}",
                METADATA: {"tool.name": tool_name, "mcp.operation": "call_tool"},
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
        tool_name = "unknown_tool"

        if "instance" in kwargs and hasattr(kwargs["instance"], "name"):
            tool_name = getattr(kwargs["instance"], "name", "unknown_tool")
        elif "req" in kwargs and hasattr(kwargs["req"], "params"):
            params = kwargs["req"].params
            if hasattr(params, "name"):
                tool_name = params.name

        tool_arguments = get_argument_value(args, kwargs, 0, "arguments", optional=True) or {}

        span._set_ctx_items(
            {
                NAME: f"MCP Server Tool Execute: {tool_name}",
                METADATA: {"tool.name": tool_name, "mcp.operation": "execute_tool"},
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
