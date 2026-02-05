from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.trace import Span


log = get_logger(__name__)

CLAUDE_OPTIONS_KEYS = ("max_turns", "max_thinking_tokens", "max_budget_usd")


class ClaudeAgentSdkIntegration(BaseLLMIntegration):
    _integration_name = "claude_agent_sdk"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True) or ""
        model = span.get_tag("claude_agent_sdk.request.model") or ""

        metadata = self._extract_metadata(kwargs)

        input_messages = self._extract_input_messages(prompt)

        output_messages: List[Message] = [Message(content="")]
        metrics: Dict[str, int] = {}
        if not span.error and response is not None:
            output_messages, metrics = self._extract_output_data(response)

        span._set_ctx_items(
            {
                SPAN_KIND: "agent",
                MODEL_NAME: model or "",
                MODEL_PROVIDER: "claude_agent_sdk",
                INPUT_VALUE: input_messages,
                METADATA: metadata,
                OUTPUT_VALUE: output_messages,
                METRICS: metrics,
            }
        )

    def _extract_metadata(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        metadata = {}
        options = kwargs.get("options")
        for key in CLAUDE_OPTIONS_KEYS:
            if hasattr(options, key) and getattr(options, key):
                metadata[key] = getattr(options, key)
        self._format_context(metadata, kwargs)
        return metadata

    def _parse_context_categories(self, context_messages: List[Any]) -> Dict[str, Any]:
        """Parse category percentages and token counts from context UserMessage.

        Args:
            context_messages: List of messages from /context query

        Returns:
            Dictionary with:
                - "categories": Dict mapping category names to percentage strings
                - "used_tokens": Token count string (e.g., "16.3k") or None
                - "total_tokens": Token count string (e.g., "200.0k") or None
        """
        result = {"categories": {}, "used_tokens": None, "total_tokens": None}

        if not context_messages or not isinstance(context_messages, list):
            return result

        # Find the UserMessage containing the context table
        for msg in context_messages:
            msg_type = type(msg).__name__
            if msg_type == "UserMessage":
                content = _get_attr(msg, "content", "")
                if not content or not isinstance(content, str):
                    continue

                lines = content.split("\n")
                in_category_table = False

                for i, line in enumerate(lines):
                    # Parse token usage line: "**Tokens:** 16.3k / 200.0k (8%)"
                    if "**Tokens:**" in line:
                        try:
                            # Extract the token counts from format: "16.3k / 200.0k"
                            tokens_part = line.split("**Tokens:**")[1].split("(")[0].strip()
                            if "/" in tokens_part:
                                used_str, total_str = [t.strip() for t in tokens_part.split("/")]
                                result["used_tokens"] = used_str
                                result["total_tokens"] = total_str
                        except (IndexError, ValueError):
                            pass

                    # Start parsing when we find the category section
                    if "### Estimated usage by category" in line:
                        in_category_table = True
                        continue

                    # Stop when we hit the next section
                    if in_category_table and line.startswith("###"):
                        break

                    # Parse table rows only in the category section
                    if in_category_table and "|" in line:
                        # Skip separator lines (contain only dashes and pipes)
                        if line.strip().replace("|", "").replace("-", "").strip() == "":
                            continue

                        parts = [p.strip() for p in line.split("|")]
                        # parts[0] is empty (before first |), parts[1] is category, parts[3] is percentage
                        if len(parts) >= 4 and parts[1] and parts[3]:
                            category = parts[1]
                            percentage = parts[3]
                            # Skip header row
                            if category != "Category" and percentage != "Percentage":
                                result["categories"][category] = percentage

                break  # Only process the first UserMessage

        return result

    def _format_context(self, parameters: Dict[str, Any], kwargs: Dict[str, Any]) -> None:
        """Format context from kwargs.

        Extracts category usage percentages and token counts from before and after context messages
        and stores them in the parameters.
        """
        after_context = kwargs.get("_dd_context")
        before_context = kwargs.get("_dd_before_context")

        if after_context:
            parameters["after_context"] = self._parse_context_categories(after_context)

        if before_context:
            parameters["before_context"] = self._parse_context_categories(before_context)

    def _extract_input_messages(self, prompt: str) -> List[Message]:
        return [Message(content=prompt, role="user")]

    def _extract_output_data(self, response: Any) -> Tuple[List[Message], Dict[str, int]]:
        output_messages: List[Message] = []
        metrics: Dict[str, int] = {}

        if not response or not isinstance(response, list):
            return [Message(content="")], metrics

        for msg in response:
            msg_type = type(msg).__name__
            if msg_type == "AssistantMessage":
                content_blocks = _get_attr(msg, "content", []) or []
                if not isinstance(content_blocks, list):
                    continue
                for block in content_blocks:
                    block_type = type(block).__name__
                    if block_type == "TextBlock":
                        text = _get_attr(block, "text", "")
                        if text:
                            output_messages.append(Message(content=str(text), role="assistant"))
                    elif block_type == "ToolUseBlock":
                        tool_name = _get_attr(block, "name", "unknown_tool")
                        tool_id = _get_attr(block, "id", "")
                        tool_input = _get_attr(block, "input", {})
                        tool_call = ToolCall(
                            name=str(tool_name),
                            arguments=tool_input if isinstance(tool_input, dict) else {},
                            tool_id=str(tool_id),
                            type="tool_use",
                        )
                        output_messages.append(Message(content="", role="assistant", tool_calls=[tool_call]))
            elif msg_type == "ResultMessage" and not metrics:
                metrics = self._extract_result_message(msg)

        return output_messages or [Message(content="")], metrics

    def _extract_result_message(self, message: Any) -> Dict[str, int]:
        metrics: Dict[str, int] = {}
        usage = _get_attr(message, "usage", None) or {}
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens") or 0
            output_tokens = usage.get("output_tokens") or 0
            cache_creation = usage.get("cache_creation_input_tokens") or 0
            cache_read = usage.get("cache_read_input_tokens") or 0

            if input_tokens:
                metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens + cache_creation + cache_read
            if output_tokens:
                metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
            if input_tokens and output_tokens:
                metrics[TOTAL_TOKENS_METRIC_KEY] = metrics[INPUT_TOKENS_METRIC_KEY] + metrics[OUTPUT_TOKENS_METRIC_KEY]
            if cache_creation:
                metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_creation
            if cache_read:
                metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cache_read
        return metrics
