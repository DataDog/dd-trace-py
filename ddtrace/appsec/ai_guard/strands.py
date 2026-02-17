"""AI Guard Hook Provider for AWS Strands Agents SDK.

Usage::

    from ddtrace.appsec.ai_guard.strands import AIGuardStrandsHookProvider

    agent = Agent(
        model=model,
        hooks=[AIGuardStrandsHookProvider()],
    )

The hook provider evaluates messages at key lifecycle points:
- ``before_model_invocation``: Scans user prompts before sending to the LLM
- ``after_model_invocation``: Scans model responses for policy violations
- ``before_tool_invocation``: Scans tool calls before execution
"""

import json
from typing import Any
from typing import Optional

from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
from ddtrace.appsec.ai_guard._api_client import new_ai_guard_client
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)

try:
    from strands.agent.hooks import HookProvider as _StrandsHookProvider
except ImportError:
    _StrandsHookProvider = object


class AIGuardStrandsHookProvider(_StrandsHookProvider):
    """AI Guard security hook provider for Strands Agents.

    Evaluates messages sent to LLMs and tool calls against Datadog AI Guard
    security policies. When a policy violation is detected and blocking is
    enabled, raises ``AIGuardAbortError`` to prevent the operation.

    :param client: Optional pre-configured ``AIGuardClient``. If not provided,
        one is created via ``new_ai_guard_client()``.
    """

    def __init__(self, client: Optional[AIGuardClient] = None):
        self._client = client or new_ai_guard_client()

    def before_model_invocation(self, **kwargs: Any) -> None:
        """Evaluate prompt messages before sending to the model.

        Converts Strands messages (Bedrock Converse format) to AI Guard
        format and evaluates them. Raises ``AIGuardAbortError`` if the
        prompt violates a security policy.
        """
        try:
            messages = kwargs.get("messages", [])
            system_prompt = kwargs.get("system_prompt", None)
            ai_guard_messages = _convert_strands_messages(messages, system_prompt)
            if ai_guard_messages:
                self._client.evaluate(ai_guard_messages, Options(block=True))
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate model invocation", exc_info=True)

    def after_model_invocation(self, **kwargs: Any) -> None:
        """Evaluate model response for policy violations.

        Scans the model's output message for content that violates
        security policies (e.g., PII in responses, harmful content).
        """
        try:
            message = kwargs.get("message", {})
            if not message:
                return
            ai_guard_messages = _convert_strands_messages([message])
            if ai_guard_messages:
                self._client.evaluate(ai_guard_messages, Options(block=True))
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate model response", exc_info=True)

    def before_tool_invocation(self, **kwargs: Any) -> None:
        """Evaluate a tool call before execution.

        Builds the conversation history including the pending tool call
        and evaluates it against security policies.
        """
        try:
            tool = kwargs.get("tool", {})
            if not tool:
                return
            tool_name = tool.get("name", "")
            tool_use_id = tool.get("toolUseId", "")
            tool_input = tool.get("input", {})

            messages = kwargs.get("messages", [])
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
            self._client.evaluate(ai_guard_messages, Options(block=True))
        except AIGuardAbortError:
            raise
        except Exception:
            logger.debug("Failed to evaluate tool invocation", exc_info=True)


def _try_format_json(value: Any) -> str:
    if not value:
        return ""
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def _extract_text_from_content(content: Any) -> str:
    """Extract text from a Bedrock Converse content block list.

    Content blocks are lists of dicts, each with a single key indicating
    the type: ``{"text": "..."}`` or other types like ``toolUse``, ``toolResult``.
    This function extracts and joins all text blocks.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        if isinstance(block, str):
            texts.append(block)
        elif isinstance(block, dict) and "text" in block:
            texts.append(block["text"])
    return " ".join(texts) if texts else ""


def _extract_tool_uses(content: list) -> list[dict]:
    """Extract toolUse blocks from a Bedrock Converse content list."""
    if not isinstance(content, list):
        return []
    return [block["toolUse"] for block in content if isinstance(block, dict) and "toolUse" in block]


def _extract_tool_results(content: list) -> list[dict]:
    """Extract toolResult blocks from a Bedrock Converse content list."""
    if not isinstance(content, list):
        return []
    return [block["toolResult"] for block in content if isinstance(block, dict) and "toolResult" in block]


def _extract_tool_result_text(tool_result: dict) -> str:
    """Extract text content from a toolResult block."""
    result_content = tool_result.get("content", [])
    if not isinstance(result_content, list):
        return str(result_content) if result_content else ""
    texts = []
    for entry in result_content:
        if isinstance(entry, dict):
            if "text" in entry:
                texts.append(entry["text"])
            elif "json" in entry:
                texts.append(_try_format_json(entry["json"]))
    return " ".join(texts) if texts else ""


def _convert_strands_messages(
    messages: list[dict],
    system_prompt: Any = None,
) -> list[Message]:
    """Convert Strands/Bedrock Converse messages to AI Guard format.

    :param messages: List of Bedrock Converse message dicts with ``role``
        and ``content`` fields.
    :param system_prompt: Optional system prompt (string or content block list).
    :returns: List of AI Guard ``Message`` dicts.
    """
    result: list[Message] = []

    if system_prompt:
        system_text = _extract_text_from_content(system_prompt)
        if system_text:
            result.append(Message(role="system", content=system_text))

    for msg in messages:
        try:
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            content = msg.get("content", [])

            if role == "user":
                text = _extract_text_from_content(content)
                tool_results = _extract_tool_results(content)

                if text:
                    result.append(Message(role="user", content=text))

                for tr in tool_results:
                    result.append(
                        Message(
                            role="tool",
                            tool_call_id=tr.get("toolUseId", ""),
                            content=_extract_tool_result_text(tr),
                        )
                    )

            elif role == "assistant":
                tool_uses = _extract_tool_uses(content)
                text = _extract_text_from_content(content)

                if tool_uses:
                    tool_calls = [
                        ToolCall(
                            id=tu.get("toolUseId", ""),
                            function=Function(
                                name=tu.get("name", ""),
                                arguments=_try_format_json(tu.get("input", {})),
                            ),
                        )
                        for tu in tool_uses
                    ]
                    result.append(Message(role="assistant", tool_calls=tool_calls))

                if text:
                    result.append(Message(role="assistant", content=text))

        except Exception:
            logger.debug("Failed to convert message", exc_info=True)

    return result
