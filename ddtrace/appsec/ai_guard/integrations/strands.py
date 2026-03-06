"""AI Guard Hook Provider for AWS Strands Agents SDK.

Usage::

    from ddtrace.appsec.ai_guard import AIGuardStrandsHookProvider

    agent = Agent(
        model=model,
        hooks=[AIGuardStrandsHookProvider()]
    )

The hook provider evaluates messages at key lifecycle points:
- ``BeforeModelCallEvent``: Scans user prompts before sending to the LLM
  (skips tool output messages already processed by ``AfterToolCallEvent``)
- ``AfterModelCallEvent``: Scans model text responses for policy violations
  (skips tool calls, which are analyzed individually in ``BeforeToolCallEvent``)
- ``BeforeToolCallEvent``: Scans tool calls before execution
- ``AfterToolCallEvent``: Scans tool results after execution

Parameters:
- ``detailed_error`` (bool): Include AI Guard reasons in blocked messages (default: False)
- ``retry`` (bool): Use the Strands retry mechanism on AfterModel/AfterToolCall blocks (default: False)
- ``raise_error`` (bool): Raise ``AIGuardAbortError`` instead of replacing output (default: False)
"""

from typing import Any

from strands.hooks import AfterModelCallEvent as _AfterModelCallEvent
from strands.hooks import AfterToolCallEvent as _AfterToolCallEvent
from strands.hooks import BeforeModelCallEvent as _BeforeModelCallEvent
from strands.hooks import BeforeToolCallEvent as _BeforeToolCallEvent
from strands.hooks import HookProvider as _StrandsHookProvider
from strands.hooks import HookRegistry as _StrandsHookRegistry

from ddtrace.appsec._ai_guard.messages import try_format_json
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
from ddtrace.appsec.ai_guard._api_client import new_ai_guard_client
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)

_BLOCKED_MSG = "[DATADOG AI GUARD] has been canceled for security reasons"
_BLOCKED_TOOL_MSG = "[DATADOG AI GUARD] '{}' has been canceled for security reasons"


class AIGuardStrandsHookProvider(_StrandsHookProvider):
    """AI Guard security hook provider for Strands Agents.

    Evaluates messages sent to LLMs and tool calls against Datadog AI Guard
    security policies. When a policy violation is detected, the default behavior
    is to replace the offending content with a blocked message. Use the
    ``raise_error`` parameter to raise ``AIGuardAbortError`` instead.

    :param detailed_error: If True, append the AI Guard reason to blocked messages.
    :param retry: If True, set ``event.retry = True`` on AfterModel/AfterToolCall blocks
        so Strands retries the operation.
    :param raise_error: If True, raise ``AIGuardAbortError`` instead of replacing output.
    """

    def __init__(self, *, detailed_error: bool = False, retry: bool = False, raise_error: bool = False):
        self._client: AIGuardClient = new_ai_guard_client()
        self._detailed_error = detailed_error
        self._retry = retry
        self._raise_error = raise_error
        logger.debug("AIGuardStrandsHookProvider initialized with client: %s", self._client)

    def register_hooks(self, registry: _StrandsHookRegistry, **kwargs: Any) -> None:
        """Register AI Guard callbacks for agent lifecycle events."""
        registry.add_callback(_BeforeModelCallEvent, self._on_before_model_call)
        registry.add_callback(_AfterModelCallEvent, self._on_after_model_call)
        registry.add_callback(_AfterToolCallEvent, self._on_after_tool_call)
        registry.add_callback(_BeforeToolCallEvent, self._on_before_tool_call)

    def _blocked_message(self, tool_name: str | None = None, reason: str | None = None) -> str:
        if tool_name:
            msg = _BLOCKED_TOOL_MSG.format(tool_name)
        else:
            msg = _BLOCKED_MSG
        if self._detailed_error and reason:
            msg += f": {reason}"
        return msg

    def _on_before_model_call(self, event: _BeforeModelCallEvent) -> None:
        """Evaluate prompt messages before sending to the model.

        Skips tool output messages (already processed in AfterToolCall).
        On block: replaces the last user message content with a blocked message,
        or raises ``AIGuardAbortError`` if ``raise_error`` is True.
        """
        try:
            logger.debug("AIGuard event: %s", event)
            messages = event.agent.messages
            system_prompt = event.agent.system_prompt
            # Exclude_tool_results=True because tool outputs were
            # already scanned in AfterToolCall; re-scanning would be redundant.
            ai_guard_messages = _convert_strands_messages(messages, system_prompt, exclude_tool_results=True)
            logger.debug("AIGuard messages: %s", ai_guard_messages)
            if ai_guard_messages:
                result = self._client.evaluate(ai_guard_messages, Options(block=True))
                logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError as e:
            if self._raise_error:
                raise
            blocked_text = self._blocked_message(reason=e.reason)
            # Replace the last user message content so the model sees the blocked text
            for msg in reversed(event.agent.messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    msg["content"] = [{"text": blocked_text}]
                    break
        except Exception:
            logger.debug("Failed to evaluate model invocation", exc_info=True)

    def _on_after_model_call(self, event: _AfterModelCallEvent) -> None:
        """Evaluate model response for policy violations.

        Only analyzes assistant text content (tool calls are analyzed
        individually in BeforeToolCall). On block: replaces the response text
        with a blocked message and optionally retries.
        """
        try:
            logger.debug("AIGuard event: %s", event)
            if not event.stop_response:
                return
            logger.debug("AIGuard message: %s", event.stop_response.message)
            message = event.stop_response.message
            if not message:
                return
            # Only analyze text content from the assistant response.
            # Tool calls (toolUse blocks) are analyzed individually in BeforeToolCall.
            text_only_message = {
                "role": message.get("role", "assistant"),
                "content": [block for block in message.get("content", []) if "text" in block],
            }
            if not text_only_message["content"]:
                return
            ai_guard_messages = _convert_strands_messages([text_only_message])
            if ai_guard_messages:
                result = self._client.evaluate(ai_guard_messages, Options(block=True))
                logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError as e:
            if self._raise_error:
                raise
            blocked_text = self._blocked_message(reason=e.reason)
            # Replace text content in the response message
            for block in event.stop_response.message.get("content", []):
                if "text" in block:
                    block["text"] = blocked_text
            if self._retry:
                event.retry = True
        except Exception:
            logger.debug("Failed to evaluate model invocation", exc_info=True)

    def _on_after_tool_call(self, event: _AfterToolCallEvent) -> None:
        """Evaluate tool result after execution.

        Builds the conversation history including the tool call and its result,
        then evaluates against security policies. On block: replaces the tool
        result content with a blocked message.
        """
        tool_name = ""
        try:
            logger.debug("AIGuard event: %s", event)
            logger.debug("AIGuard agent: %s", event.agent)
            if event.agent:
                logger.debug("AIGuard message: %s", event.agent.messages)
            tool_use = event.tool_use
            tool_name = tool_use.get("name", "")
            tool_use_id = tool_use.get("toolUseId", "")
            tool_input = tool_use.get("input", {})
            tool_result = event.result

            messages = event.agent.messages
            ai_guard_messages = _convert_strands_messages(messages)

            # Append the tool call (assistant message)
            ai_guard_messages.append(
                Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id=tool_use_id,
                            function=Function(
                                name=tool_name,
                                arguments=try_format_json(tool_input),
                            ),
                        )
                    ],
                )
            )

            # Append the tool result
            ai_guard_messages.append(
                Message(
                    role="tool",
                    tool_call_id=tool_use_id,
                    content=_tool_result_text(tool_result),
                )
            )

            result = self._client.evaluate(ai_guard_messages, Options(block=True))
            logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError as e:
            if self._raise_error:
                raise
            blocked_text = self._blocked_message(tool_name=tool_name, reason=e.reason)
            # Replace the tool result content
            content = event.result.get("content", [])
            if content:
                content[0]["text"] = blocked_text
            else:
                event.result["content"] = [{"text": blocked_text}]
            if self._retry:
                event.retry = True
        except Exception:
            logger.debug("Failed to evaluate tool result", exc_info=True)

    def _on_before_tool_call(self, event: _BeforeToolCallEvent) -> None:
        """Evaluate a tool call before execution.

        Builds the conversation history including the pending tool call
        and evaluates it against security policies. On block: cancels the tool
        with a descriptive message.
        """
        tool_name = ""
        try:
            logger.debug("AIGuard event: %s", event)
            logger.debug("AIGuard agent: %s", event.agent)
            if event.agent:
                logger.debug("AIGuard message: %s", event.agent.messages)
            tool_use = event.tool_use
            tool_name = tool_use.get("name", "")
            tool_use_id = tool_use.get("toolUseId", "")
            tool_input = tool_use.get("input", {})

            messages = event.agent.messages
            ai_guard_messages = _convert_strands_messages(messages)
            ai_guard_messages.append(
                Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(
                            id=tool_use_id,
                            function=Function(
                                name=tool_name,
                                arguments=try_format_json(tool_input),
                            ),
                        )
                    ],
                )
            )
            result = self._client.evaluate(ai_guard_messages, Options(block=True))
            logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError as e:
            if self._raise_error:
                raise
            event.cancel_tool = self._blocked_message(tool_name=tool_name, reason=e.reason)
        except Exception:
            logger.debug("Failed to evaluate tool invocation", exc_info=True)


def _tool_result_text(tool_result: dict) -> str:
    """Extract text from a Bedrock Converse ToolResult.

    ToolResultContent entries may contain ``text`` or ``json`` keys.
    """
    texts = []
    for entry in tool_result.get("content", []):
        if "text" in entry:
            texts.append(entry["text"])
        elif "json" in entry:
            texts.append(try_format_json(entry["json"]))
    return " ".join(texts)


def _convert_strands_messages(
    messages: list[dict],
    system_prompt: str | None = None,
    exclude_tool_results: bool = False,
) -> list[Message]:
    """Convert Strands/Bedrock Converse messages to AI Guard format.

    Strands messages use the Bedrock Converse schema: each message has a
    ``role`` ("user" | "assistant") and ``content`` (list of ContentBlock
    dicts with optional keys ``text``, ``toolUse``, ``toolResult``, etc.).

    :param messages: List of Bedrock Converse message dicts.
    :param system_prompt: Optional system prompt string (``agent.system_prompt``).
    :param exclude_tool_results: If True, skip toolResult blocks (already
        processed in AfterToolCall).
    :returns: List of AI Guard ``Message`` objects.
    """
    result: list[Message] = []

    if system_prompt:
        result.append(Message(role="system", content=system_prompt))

    for msg in messages:
        try:
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            # Collect text from all text content blocks in this message
            texts = [block["text"] for block in content if "text" in block]

            if role == "user":
                if texts:
                    result.append(Message(role="user", content=" ".join(texts)))

                # In Bedrock Converse format, tool results appear in user messages
                if not exclude_tool_results:
                    for block in content:
                        if tr := block.get("toolResult"):
                            result.append(
                                Message(
                                    role="tool",
                                    tool_call_id=tr.get("toolUseId", ""),
                                    content=_tool_result_text(tr),
                                )
                            )

            elif role == "assistant":
                tool_uses = [block["toolUse"] for block in content if "toolUse" in block]
                if tool_uses:
                    result.append(
                        Message(
                            role="assistant",
                            tool_calls=[
                                ToolCall(
                                    id=tu.get("toolUseId", ""),
                                    function=Function(
                                        name=tu.get("name", ""),
                                        arguments=try_format_json(tu.get("input", {})),
                                    ),
                                )
                                for tu in tool_uses
                            ],
                        )
                    )
                if texts:
                    result.append(Message(role="assistant", content=" ".join(texts)))

        except Exception:
            logger.debug("Failed to convert message", exc_info=True)

    return result
