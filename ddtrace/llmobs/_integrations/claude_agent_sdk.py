from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.trace import Span


log = get_logger(__name__)


MODEL_TAG = "claude_agent_sdk.request.model"


class ClaudeAgentSdkIntegration(BaseLLMIntegration):
    """LLMObs integration for claude_agent_sdk.

    The claude-agent-sdk communicates with Claude Code via subprocess/CLI.
    Response messages are yielded as a stream of typed objects:
    - SystemMessage: initialization info (contains model, tools, session_id)
    - AssistantMessage: assistant responses with content blocks (TextBlock, ToolUseBlock)
    - ResultMessage: completion info with usage metrics (input_tokens, output_tokens, etc.)
    """

    _integration_name = "claude_agent_sdk"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all claude_agent_sdk spans."""
        if model is not None:
            span._set_tag_str(MODEL_TAG, model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response tags from a query and set them as temporary "_ml_obs.*" tags.

        For standalone query():
            args: ()
            kwargs: {"prompt": str, "options": ClaudeAgentOptions}
            response: List of messages [SystemMessage, AssistantMessage, ResultMessage, ...]

        For ClaudeSDKClient.query():
            args: (prompt,) or ()
            kwargs: {"prompt": str, "session_id": str} or similar
            response: None (client.query() just sends, doesn't receive)
        """
        # Extract prompt from args or kwargs
        prompt = ""
        if args:
            prompt = str(args[0]) if args[0] else ""
        if not prompt:
            prompt = str(kwargs.get("prompt", ""))

        # Extract model from response messages (SystemMessage has model info)
        model = self._extract_model_from_response(response)
        if model:
            span._set_tag_str(MODEL_TAG, model)

        # Extract options/parameters
        parameters = {}
        options = kwargs.get("options")
        if options:
            if hasattr(options, "max_turns") and options.max_turns:
                parameters["max_turns"] = options.max_turns
            if hasattr(options, "temperature") and options.temperature:
                parameters["temperature"] = options.temperature

        # Build input messages from prompt
        input_messages = self._extract_input_messages(prompt)

        # Extract output messages from response
        output_messages: List[Message] = [Message(content="")]
        if not span.error and response is not None:
            output_messages = self._extract_output_messages(response)

        # Extract usage metrics from ResultMessage
        metrics = self._extract_usage(response) if response else {}

        span._set_ctx_items(
            {
                SPAN_KIND: "agent",
                MODEL_NAME: model or "",
                MODEL_PROVIDER: "claude_agent_sdk",
                INPUT_VALUE: input_messages,
                METADATA: parameters,
                OUTPUT_VALUE: output_messages,
                METRICS: metrics,
            }
        )

    def _extract_model_from_response(self, response: Any) -> str:
        """Extract model name from response messages.

        The model can be found in:
        1. SystemMessage.data.model (init message)
        2. AssistantMessage.model
        """
        if not response or not isinstance(response, list):
            return ""

        for msg in response:
            msg_type = type(msg).__name__

            # Check AssistantMessage.model
            if msg_type == "AssistantMessage":
                model = _get_attr(msg, "model", None)
                if model:
                    return str(model)

            # Check SystemMessage.data.model
            if msg_type == "SystemMessage":
                data = _get_attr(msg, "data", None)
                if data and isinstance(data, dict):
                    model = data.get("model")
                    if model:
                        return str(model)

        return ""

    def _extract_input_messages(self, prompt: Any) -> List[Message]:
        """Extract input messages from the prompt.

        The claude-agent-sdk takes a simple string prompt, not a message list.
        """
        if isinstance(prompt, str) and prompt:
            return [Message(content=prompt, role="user")]
        return []

    def _extract_output_messages(self, response: Any) -> List[Message]:
        """Extract output messages from response messages.

        Response is a list of messages:
        - AssistantMessage has content list with TextBlock, ToolUseBlock, etc.
        - We extract text content from AssistantMessage.content[].text
        """
        output_messages: List[Message] = []

        if not response or not isinstance(response, list):
            return [Message(content="")]

        for msg in response:
            msg_type = type(msg).__name__

            if msg_type == "AssistantMessage":
                content_blocks = _get_attr(msg, "content", [])
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        block_type = type(block).__name__

                        if block_type == "TextBlock":
                            text = _get_attr(block, "text", "")
                            if text:
                                output_messages.append(Message(content=str(text), role="assistant"))

                        elif block_type == "ToolUseBlock":
                            # Tool use block - capture tool name, id, and input arguments
                            tool_name = _get_attr(block, "name", "unknown_tool")
                            tool_id = _get_attr(block, "id", "")
                            tool_input = _get_attr(block, "input", {})

                            # Build tool call info following Anthropic pattern
                            tool_call = ToolCall(
                                name=str(tool_name),
                                arguments=tool_input if isinstance(tool_input, dict) else {},
                                tool_id=str(tool_id),
                                type="tool_use",
                            )

                            output_messages.append(Message(content="", role="assistant", tool_calls=[tool_call]))

        return output_messages if output_messages else [Message(content="")]

    def _extract_usage(self, response: Any) -> Dict[str, int]:
        """Extract token usage from ResultMessage.

        ResultMessage contains usage dict with:
        - input_tokens: number of input tokens
        - output_tokens: number of output tokens
        - cache_creation_input_tokens: tokens used to create cache
        - cache_read_input_tokens: tokens read from cache
        """
        metrics: Dict[str, int] = {}

        if not response or not isinstance(response, list):
            return metrics

        for msg in response:
            msg_type = type(msg).__name__

            if msg_type == "ResultMessage":
                usage = _get_attr(msg, "usage", None)
                if usage and isinstance(usage, dict):
                    input_tokens = usage.get("input_tokens")
                    output_tokens = usage.get("output_tokens")

                    # Include cache tokens in total input if available
                    cache_creation = usage.get("cache_creation_input_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)

                    if input_tokens is not None:
                        # Total input includes base + cache tokens
                        total_input = input_tokens + cache_creation + cache_read
                        metrics[INPUT_TOKENS_METRIC_KEY] = total_input

                    if output_tokens is not None:
                        metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens

                    if INPUT_TOKENS_METRIC_KEY in metrics and OUTPUT_TOKENS_METRIC_KEY in metrics:
                        metrics[TOTAL_TOKENS_METRIC_KEY] = (
                            metrics[INPUT_TOKENS_METRIC_KEY] + metrics[OUTPUT_TOKENS_METRIC_KEY]
                        )

                # Only need the first ResultMessage
                break

        return metrics
