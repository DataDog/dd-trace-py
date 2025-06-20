from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        """Set base span tags for MCP operations."""
        pass

    def llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Extract input/output information from the request and response to be submitted to LLMObs."""
        if not self.llmobs_enabled:
            return
        try:
            self._llmobs_set_tags(span, args, kwargs, response, operation, headers)
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
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Set LLMObs tags for MCP tool operations."""
        span._set_ctx_item(SPAN_KIND, "tool")

        if operation == "call_tool":
            self._llmobs_set_tags_client_call(span, args, kwargs, response)
        elif operation == "execute_tool":
            self._llmobs_set_tags_server_execute(span, args, kwargs, response, headers)

    def _llmobs_set_tags_client_call(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any]
    ):
        """Set LLMObs tags for client-side tool calls."""
        # Extract tool name from args/kwargs
        tool_name = get_argument_value(args, kwargs, 0, "name", optional=True) or "unknown_tool"

        # Extract tool arguments
        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}

        metadata = {
            "tool.name": tool_name,
            "mcp.operation": "call_tool",
        }

        span._set_ctx_items(
            {
                NAME: f"MCP Client Tool Call: {tool_name}",
                METADATA: metadata,
                INPUT_VALUE: tool_arguments,
            }
        )

        if span.error:
            return

        # Extract output from response
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
        headers: Optional[Dict[str, str]] = None,
    ):
        """Set LLMObs tags for server-side tool execution."""
        # Extract tool name - depends on MCP server implementation
        tool_name = "unknown_tool"

        # For FastMCP Tool.run() - tool name is in instance.name
        if "instance" in kwargs and hasattr(kwargs["instance"], "name"):
            tool_name = getattr(kwargs["instance"], "name", "unknown_tool")

        # For low-level server handlers - extract from request params
        elif "req" in kwargs and hasattr(kwargs["req"], "params"):
            params = kwargs["req"].params
            if hasattr(params, "name"):
                tool_name = params.name

        # Extract tool arguments (headers already extracted and activated in patch.py)
        tool_arguments = get_argument_value(args, kwargs, 0, "arguments", optional=True) or {}

        metadata = {
            "tool.name": tool_name,
            "mcp.operation": "execute_tool",
        }

        span._set_ctx_items(
            {
                NAME: f"MCP Server Tool Execute: {tool_name}",
                METADATA: metadata,
                INPUT_VALUE: tool_arguments,
            }
        )

        if span.error:
            return

        # Set output value
        output_value = response
        if hasattr(response, "content"):
            output_value = getattr(response, "content", None)
        elif hasattr(response, "result"):
            output_value = getattr(response, "result", None)

        span._set_ctx_item(OUTPUT_VALUE, output_value)
