from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import AGENT_MANIFEST
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
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolResult
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
        if operation == "tool":
            self._llmobs_set_tool_tags(span, kwargs)
        else:
            self._llmobs_set_agent_tags(span, args, kwargs, response)

    def _llmobs_set_tool_tags(self, span: Span, kwargs: Dict[str, Any]) -> None:
        tool_input = kwargs.get("tool_input", {})
        tool_output = kwargs.get("tool_output", "")

        span._set_ctx_items(
            {
                SPAN_KIND: "tool",
                INPUT_VALUE: tool_input,
                OUTPUT_VALUE: tool_output,
            }
        )

    def _llmobs_set_agent_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True) or ""
        model = span.get_tag("claude_agent_sdk.request.model") or ""

        metadata = self._extract_metadata(kwargs)

        input_messages = self._extract_input_messages(prompt)

        output_messages: List[Message] = [Message(content="")]
        metrics: Dict[str, int] = {}
        init_system_message: Dict[str, Any] = {}
        if not span.error and response is not None:
            output_messages, metrics, init_system_message = self._extract_output_data(response)

        agent_manifest = self._build_agent_manifest(model, metadata, init_system_message)

        span._set_ctx_items(
            {
                SPAN_KIND: "agent",
                MODEL_NAME: model or "",
                INPUT_VALUE: input_messages,
                METADATA: metadata,
                OUTPUT_VALUE: output_messages,
                METRICS: metrics,
                AGENT_MANIFEST: agent_manifest,
            }
        )

    def _build_agent_manifest(
        self, model: str, metadata: Dict[str, Any], init_system_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        manifest: Dict[str, Any] = {}
        manifest["framework"] = "Claude Agent SDK"
        if model:
            manifest["model"] = model
        if init_system_message:
            tools = init_system_message.get("tools", []) or []
            manifest["tools"] = [{"name": tool} for tool in tools]
        if init_system_message:
            mcp_servers = init_system_message.get("mcp_servers", []) or []
            manifest["dependencies"] = {"mcp_servers": mcp_servers}
        if "max_turns" in metadata:
            manifest["max_iterations"] = metadata["max_turns"]
        return manifest

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
        result: Dict[str, Any] = {"categories": {}, "used_tokens": None, "total_tokens": None}

        if not context_messages or not isinstance(context_messages, list):
            return result

        try:
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
        except Exception:
            log.warning("Error parsing context categories", exc_info=True)

        return result

    def _format_context(self, parameters: Dict[str, Any], kwargs: Dict[str, Any]) -> None:
        after_context = kwargs.get("_dd_context")
        before_context = kwargs.get("_dd_before_context")

        if after_context:
            parameters["after_context"] = self._parse_context_categories(after_context)

        if before_context:
            parameters["before_context"] = self._parse_context_categories(before_context)

    def _extract_input_messages(self, prompt: str) -> List[Message]:
        return [Message(content=prompt, role="user")]

    def _parse_content_blocks(self, content: Any, role: str) -> List[Message]:
        """Parses content which can be a string or a list of content blocks
        (TextBlock, ToolUseBlock, etc.) into a list of messages.
        """
        messages: List[Message] = []

        if isinstance(content, str):
            messages.append(Message(content=content, role=role))
            return messages

        if not isinstance(content, list):
            return messages

        for block in content:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text = _get_attr(block, "text", "") or ""
                messages.append(Message(content=str(text), role=role))
            elif block_type == "ThinkingBlock":
                thinking = _get_attr(block, "thinking", "") or ""
                messages.append(Message(content=str(thinking), role=role))
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
                messages.append(Message(content="", role=role, tool_calls=[tool_call]))
            elif block_type == "ToolResultBlock":
                tool_use_id = _get_attr(block, "tool_use_id", "")
                result_content = _get_attr(block, "content", "")
                if isinstance(result_content, str):
                    formatted_result = result_content
                else:
                    formatted_result = safe_json(result_content) or str(result_content)
                tool_result = ToolResult(
                    result=formatted_result,
                    tool_id=str(tool_use_id),
                    type="tool_result",
                )
                messages.append(Message(content="", role=role, tool_results=[tool_result]))

        return messages

    def _extract_output_data(self, response: Any) -> Tuple[List[Message], Dict[str, int], Dict[str, Any]]:
        """Extract output data from response, including output messages, usage metrics, and init system message."""
        output_messages: List[Message] = []
        metrics: Dict[str, int] = {}
        init_system_message: Dict[str, Any] = {}

        if not response or not isinstance(response, list):
            return [Message(content="")], metrics, init_system_message

        for msg in response:
            msg_type = type(msg).__name__
            if msg_type == "AssistantMessage":
                content = _get_attr(msg, "content", []) or []
                output_messages.extend(self._parse_content_blocks(content, "assistant"))
            elif msg_type == "SystemMessage":
                data = _get_attr(msg, "data", {}) or {}
                if data:
                    init_system_message = data if isinstance(data, dict) else {}
                    content = safe_json(data) or ""
                    output_messages.append(Message(content=content, role="system"))
            elif msg_type == "UserMessage":
                content = _get_attr(msg, "content", "") or ""
                output_messages.extend(self._parse_content_blocks(content, "user"))
            elif msg_type == "ResultMessage":
                if not metrics:
                    metrics = self._extract_result_message(msg)
                result = _get_attr(msg, "result", "") or ""
                if result:
                    output_messages.append(Message(content=str(result), role="system"))

        return output_messages or [Message(content="")], metrics, init_system_message

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
