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
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


log = get_logger(__name__)


class MCPIntegration(BaseLLMIntegration):
    _integration_name = "mcp"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span._set_ctx_item(SPAN_KIND, "tool")

        tool_arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True) or {}
        if operation == "call_tool":
            tool_name = args[0] if len(args) > 0 else kwargs.get("name", "unknown_tool")
            span_name = "MCP Client Tool Call: {}".format(tool_name)
        elif operation == "execute_tool":
            tool_name = args[0] if len(args) > 0 else "unknown_tool"
            span_name = "MCP Server Tool Execute: {}".format(tool_name)
        else:
            span_name = "MCP {}".format(operation)

        span._set_ctx_items(
            {
                NAME: span_name,
                INPUT_VALUE: tool_arguments,
            }
        )
        if span.error or response is None:
            return
        # The tool output is either a `CallToolResult` pydantic object (client side) or `Any` value (server side)
        # In both cases, we can try using `model_dump` and fall back to `safe_json`.
        span._set_ctx_item(
            OUTPUT_VALUE, response.model_dump() if hasattr(response, "model_dump") else safe_json(response)
        )
