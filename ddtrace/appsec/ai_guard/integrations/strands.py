"""AI Guard Hook Provider for AWS Strands Agents SDK.

Usage::

    from ddtrace.appsec.ai_guard.strands import AIGuardStrandsHookProvider

    agent = Agent(
        model=model,
        hooks=[AIGuardStrandsHookProvider()]
    )

The hook provider evaluates messages at key lifecycle points:
- ``BeforeModelCallEvent``: Scans user prompts before sending to the LLM
- ``AfterModelCallEvent``: Scans model responses for policy violations
- ``BeforeToolCallEvent``: Scans tool calls before execution
- ``AfterToolCallEvent``: Scans tool calls after execution
"""

import json
from typing import Any

from strands.hooks import AfterModelCallEvent as _AfterModelCallEvent
from strands.hooks import AfterToolCallEvent as _AfterToolCallEvent
from strands.hooks import BeforeModelCallEvent as _BeforeModelCallEvent
from strands.hooks import BeforeToolCallEvent as _BeforeToolCallEvent
from strands.hooks import HookProvider as _StrandsHookProvider
from strands.hooks import HookRegistry as _StrandsHookRegistry

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
from ddtrace.appsec.ai_guard._api_client import new_ai_guard_client
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)


class AIGuardStrandsHookProvider(_StrandsHookProvider):
    """AI Guard security hook provider for Strands Agents.

    Evaluates messages sent to LLMs and tool calls against Datadog AI Guard
    security policies. When a policy violation is detected and blocking is
    enabled, raises ``AIGuardAbortError`` to prevent the operation.

    :param client: Optional pre-configured ``AIGuardClient``. If not provided,
        one is created via ``new_ai_guard_client()``.
    """

    def __init__(self, *args, **kwargs):
        self._client: AIGuardClient = new_ai_guard_client()
        logger.debug("AIGuardStrandsHookProvider initialized with client: %s", self._client)

    def register_hooks(self, registry: _StrandsHookRegistry, **kwargs: Any) -> None:
        """Register AI Guard callbacks for agent lifecycle events."""
        registry.add_callback(_BeforeModelCallEvent, self._on_before_model_call)
        registry.add_callback(_AfterModelCallEvent, self._on_after_model_call)
        registry.add_callback(_AfterToolCallEvent, self._on_after_tool_call)
        registry.add_callback(_BeforeToolCallEvent, self._on_before_tool_call)

    def _on_before_model_call(self, event: _BeforeModelCallEvent) -> None:
        """Evaluate prompt messages before sending to the model.

        Converts Strands messages (Bedrock Converse format) to AI Guard
        format and evaluates them. Raises ``AIGuardAbortError`` if the
        prompt violates a security policy.
        """
        try:
            logger.debug("AIGuard event: %s", event)
            messages = event.agent.messages
            system_prompt = event.agent.system_prompt
            ai_guard_messages = _convert_strands_messages(messages, system_prompt)
            logger.debug("AIGuard messages: %s", ai_guard_messages)
            if ai_guard_messages:
                result = self._client.evaluate(ai_guard_messages, Options(block=True))
                logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate model invocation", exc_info=True)

    def _on_after_model_call(self, event: _AfterModelCallEvent) -> None:
        """Evaluate model response for policy violations.

        Scans the model's output message for content that violates
        security policies (e.g., PII in responses, harmful content).
        """
        try:
            logger.debug("AIGuard event: %s", event)
            if not event.stop_response:
                return
            logger.debug("AIGuard message: %s", event.stop_response.message)
            message = event.stop_response.message
            if not message:
                return
            ai_guard_messages = _convert_strands_messages([message])
            if ai_guard_messages:
                result = self._client.evaluate(ai_guard_messages, Options(block=True))
                logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate model invocation", exc_info=True)

    def _on_after_tool_call(self, event: _AfterToolCallEvent) -> None:
        """Evaluate tool result after execution.

        Builds the conversation history including the tool call and its result,
        then evaluates against security policies.  This catches scenarios where
        a tool returns sensitive data (PII, secrets) or prompt-injection payloads
        that could hijack subsequent model calls.
        """
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
                                arguments=_try_format_json(tool_input),
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
        except AIGuardAbortError:
            pass
        except Exception:
            logger.debug("Failed to evaluate tool result", exc_info=True)

    def _on_before_tool_call(self, event: _BeforeToolCallEvent) -> None:
        """Evaluate a tool call before execution.

        Builds the conversation history including the pending tool call
        and evaluates it against security policies.
        """
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
                                arguments=_try_format_json(tool_input),
                            ),
                        )
                    ],
                )
            )
            result = self._client.evaluate(ai_guard_messages, Options(block=True))
            logger.debug("AIGuard client evaluate result: %s", result)
        except AIGuardAbortError:
            event.cancel_tool = True
        except Exception:
            logger.debug("Failed to evaluate tool invocation", exc_info=True)


def _try_format_json(value: Any) -> str:
    if not value:
        return ""
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def _tool_result_text(tool_result: dict) -> str:
    """Extract text from a Bedrock Converse ToolResult.

    ToolResultContent entries may contain ``text`` or ``json`` keys.
    """
    texts = []
    for entry in tool_result.get("content", []):
        if "text" in entry:
            texts.append(entry["text"])
        elif "json" in entry:
            texts.append(_try_format_json(entry["json"]))
    return " ".join(texts)


def _convert_strands_messages(
    messages: list[dict],
    system_prompt: str | None = None,
) -> list[Message]:
    """Convert Strands/Bedrock Converse messages to AI Guard format.

    Strands messages use the Bedrock Converse schema: each message has a
    ``role`` ("user" | "assistant") and ``content`` (list of ContentBlock
    dicts with optional keys ``text``, ``toolUse``, ``toolResult``, etc.).

    :param messages: List of Bedrock Converse message dicts.
    :param system_prompt: Optional system prompt string (``agent.system_prompt``).
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
                                        arguments=_try_format_json(tu.get("input", {})),
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
