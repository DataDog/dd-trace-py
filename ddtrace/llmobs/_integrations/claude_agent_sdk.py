from typing import Any
from typing import Optional

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
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        if operation == "tool":
            self._llmobs_set_tool_tags(span, kwargs)
        else:
            self._llmobs_set_agent_tags(span, args, kwargs, response)

    def _llmobs_set_tool_tags(self, span: Span, kwargs: dict[str, Any]) -> None:
        tool_input = kwargs.get("tool_input", {})
        tool_output = kwargs.get("tool_output", "")
        tool_id = kwargs.get("tool_id", "")

        span._set_ctx_items(
            {
                SPAN_KIND: "tool",
                INPUT_VALUE: tool_input,
                OUTPUT_VALUE: tool_output,
                METADATA: {"tool_id": tool_id},
            }
        )

    def _llmobs_set_agent_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
    ) -> None:
        prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True) or ""
        model = span.get_tag("claude_agent_sdk.request.model") or ""

        metadata = self._extract_metadata(kwargs)

        input_messages = self._extract_input_messages(prompt, span)

        output_messages: list[Message] = [Message(content="")]
        metrics: dict[str, int] = {}
        init_system_message: dict[str, Any] = {}
        if not span.error and response is not None:
            output_messages, metrics, init_system_message, stop_reason = self._extract_output_data(response)
            if stop_reason:
                metadata["stop_reason"] = stop_reason

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
        self, model: str, metadata: dict[str, Any], init_system_message: dict[str, Any]
    ) -> dict[str, Any]:
        manifest: dict[str, Any] = {}
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

    def _extract_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        metadata = {}
        options = kwargs.get("options")
        for key in CLAUDE_OPTIONS_KEYS:
            if hasattr(options, key) and getattr(options, key):
                metadata[key] = getattr(options, key)
        self._format_context(metadata, kwargs)
        return metadata

    def _parse_context_delta(
        self,
        before_context: Optional[list[Any]],
        after_context: Optional[list[Any]],
    ) -> Optional[dict[str, Any]]:
        """Parse before/after /context message lists into a context_delta dict.

        Returns None if no token data could be extracted from either snapshot.
        """

        def _parse_snapshot(messages: Any) -> dict[str, Any]:
            snapshot: dict[str, Any] = {"sections": [], "used_tokens": None, "total_tokens": None}
            if not messages or not isinstance(messages, list):
                return snapshot
            try:
                for msg in messages:
                    msg_type = type(msg).__name__
                    if msg_type == "AssistantMessage":
                        blocks = _get_attr(msg, "content", []) or []
                        content = "\n".join(
                            _get_attr(b, "text", "")
                            for b in blocks
                            if type(b).__name__ == "TextBlock"
                        )
                    elif msg_type == "UserMessage":
                        content = _get_attr(msg, "content", "")
                        if isinstance(content, list):
                            content = "\n".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                    else:
                        continue
                    if not content or not isinstance(content, str):
                        continue

                    _EXCLUDED_SECTIONS = {"Free space", "Autocompact buffer"}
                    in_table = False
                    for line in content.split("\n"):
                        if "**Tokens:**" in line:
                            try:
                                tokens_part = line.split("**Tokens:**")[1].split("(")[0].strip()
                                if "/" in tokens_part:
                                    _, total_str = [t.strip() for t in tokens_part.split("/")]
                                    s = total_str.strip()
                                    snapshot["total_tokens"] = (
                                        round(float(s[:-1]) * 1000) if s.lower().endswith("k") else int(float(s))
                                    )
                            except (IndexError, ValueError, TypeError):
                                pass

                        if "### Estimated usage by category" in line:
                            in_table = True
                            continue
                        if in_table and line.startswith("###"):
                            break
                        if in_table and "|" in line:
                            if not line.strip().replace("|", "").replace("-", "").strip():
                                continue
                            parts = [p.strip() for p in line.split("|")]
                            if len(parts) >= 4 and parts[1] and parts[3]:
                                name, token_str, pct_str = parts[1], parts[2], parts[3]
                                if name == "Category" or pct_str == "Percentage":
                                    continue
                                try:
                                    pct = float(pct_str.rstrip("%"))
                                except (ValueError, TypeError):
                                    pct = 0.0
                                try:
                                    ts = token_str.strip()
                                    tokens = (
                                        round(float(ts[:-1]) * 1000) if ts.lower().endswith("k") else int(float(ts))
                                    )
                                except (ValueError, TypeError):
                                    tokens = 0
                                if name not in _EXCLUDED_SECTIONS and "(deferred)" not in name:
                                    snapshot["sections"].append({"name": name, "tokens": tokens, "pct": pct})
                    snapshot["used_tokens"] = sum(s["tokens"] for s in snapshot["sections"]) or None
                    # recompute pct as % of used tokens to match expected data format
                    if snapshot["used_tokens"]:
                        for s in snapshot["sections"]:
                            s["pct"] = round(s["tokens"] / snapshot["used_tokens"] * 100, 1)
                    break  # only process the first AssistantMessage/UserMessage
            except Exception:
                log.warning("Error parsing context snapshot", exc_info=True)
            return snapshot

        before = _parse_snapshot(before_context)
        after = _parse_snapshot(after_context)

        first_tokens = before.get("used_tokens")
        last_tokens = after.get("used_tokens")
        if first_tokens is None and last_tokens is None:
            return None

        first_tokens = first_tokens or 0
        last_tokens = last_tokens or 0
        context_window = after.get("total_tokens") or before.get("total_tokens") or 0
        first_pct = round(first_tokens / context_window * 100, 1) if context_window > 0 else 0.0
        last_pct = round(last_tokens / context_window * 100, 1) if context_window > 0 else 0.0

        delta: dict[str, Any] = {
            "first_input_tokens": first_tokens,
            "last_input_tokens": last_tokens,
            "delta_tokens": last_tokens - first_tokens,
            "context_window_size": context_window,
            "first_usage_pct": first_pct,
            "last_usage_pct": last_pct,
        }
        if before["sections"]:
            delta["first_sections"] = before["sections"]
        if after["sections"]:
            delta["last_sections"] = after["sections"]
        return delta

    def _format_context(self, metadata: dict[str, Any], kwargs: dict[str, Any]) -> None:
        after_context = kwargs.get("_dd_context")
        before_context = kwargs.get("_dd_before_context")

        context_delta = self._parse_context_delta(before_context, after_context)
        if context_delta is not None:
            if "_dd" not in metadata or not isinstance(metadata["_dd"], dict):
                metadata["_dd"] = {}
            metadata["_dd"]["context_delta"] = context_delta

    def _extract_input_messages(self, prompt: Any, span: Span) -> list[Message]:
        prompt_wrapper = span._get_ctx_item("_dd_prompt_wrapper") if span else None

        if prompt_wrapper and hasattr(prompt_wrapper, "captured_values"):
            messages = []
            for captured_msg in prompt_wrapper.captured_values:
                if isinstance(captured_msg, dict) and "message" in captured_msg:
                    message = captured_msg.get("message", {}) or {}
                    content = message.get("content", "") or ""
                    role = message.get("role", "user") or "user"
                    messages.append(Message(content=content, role=role))
                else:
                    messages.append(Message(content=safe_json(captured_msg) or "", role="user"))
            return messages

        if isinstance(prompt, str):
            return [Message(content=prompt, role="user")]

        return [Message(content="", role="user")]

    def _parse_content_blocks(self, content: Any, role: str) -> list[Message]:
        """Parses content which can be a string or a list of content blocks
        (TextBlock, ToolUseBlock, etc.) into a list of messages.
        """
        messages: list[Message] = []

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

    def _extract_output_data(self, response: Any) -> tuple[list[Message], dict[str, int], dict[str, Any], str]:
        """Extract output data from response, including output messages, usage metrics, init system message,
        and stop reason.
        """
        output_messages: list[Message] = []
        metrics: dict[str, int] = {}
        init_system_message: dict[str, Any] = {}
        stop_reason: str = ""

        if not response or not isinstance(response, list):
            return [Message(content="")], metrics, init_system_message, stop_reason

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
                if not stop_reason:
                    stop_reason = _get_attr(msg, "stop_reason", "") or ""
                if not metrics:
                    metrics = self._extract_result_message(msg)
                result = _get_attr(msg, "result", "") or ""
                if result:
                    output_messages.append(Message(content=str(result), role="assistant"))
                structured_output = _get_attr(msg, "structured_output", None)
                if structured_output is not None:
                    output_messages.append(
                        Message(content=safe_json(structured_output) or str(structured_output), role="assistant")
                    )

        return output_messages or [Message(content="")], metrics, init_system_message, stop_reason

    def _extract_result_message(self, message: Any) -> dict[str, int]:
        metrics: dict[str, int] = {}
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
