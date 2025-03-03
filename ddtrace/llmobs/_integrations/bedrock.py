from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations import BaseLLMIntegration
from ddtrace.trace import Span


log = get_logger(__name__)


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        LLMObs._instance._activate_llmobs_span(span)
        metadata = {}
        usage_metrics = {}

        ctx = args[0]
        request_params = ctx.get_item("request_params") or {}

        if ctx.get_item("stop_reason"):
            metadata["stop_reason"] = ctx["stop_reason"]
        if ctx.get_item("usage"):
            usage_metrics = ctx["usage"]

        # Translate raw usage metrics returned from bedrock to the standardized metrics format.
        if "inputTokens" in usage_metrics:
            usage_metrics["input_tokens"] = usage_metrics.pop("inputTokens")
        if "outputTokens" in usage_metrics:
            usage_metrics["output_tokens"] = usage_metrics.pop("outputTokens")
        if "totalTokens" in usage_metrics:
            usage_metrics["total_tokens"] = usage_metrics.pop("totalTokens")

        if request_params.get("temperature"):
            metadata["temperature"] = float(span.get_tag("bedrock.request.temperature") or 0.0)
        if request_params.get("max_tokens"):
            metadata["max_tokens"] = int(span.get_tag("bedrock.request.max_tokens") or 0)
        if request_params.get("top_p"):
            metadata["top_p"] = float(span.get_tag("bedrock.request.top_p") or 0.0)

        prompt = request_params.get("prompt", "")

        input_messages = self._extract_input_message(prompt)

        output_messages = [{"content": ""}]
        if not span.error and response is not None:
            output_messages = self._extract_output_message(response)
        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("bedrock.request.model") or "",
                MODEL_PROVIDER: span.get_tag("bedrock.request.model_provider") or "",
                INPUT_MESSAGES: input_messages,
                METADATA: metadata,
                METRICS: usage_metrics,
                OUTPUT_MESSAGES: output_messages,
            }
        )

    @staticmethod
    def _extract_input_message(prompt):
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
        if isinstance(prompt, str):
            return [{"content": prompt}]
        if not isinstance(prompt, list):
            log.warning("Bedrock input is not a list of messages or a string.")
            return [{"content": ""}]
        input_messages = []
        for p in prompt:
            if not isinstance(p, dict):
                continue

            role = str(p.get("role", ""))
            content = p.get("content", "")
            if isinstance(content, list):
                # Structured content array with multiple parts
                if content and isinstance(content[0], dict):
                    combined_text = ""
                    for entry in content:
                        # Text content
                        if entry.get("text"):
                            combined_text += entry.get("text", "") + " "
                        # Image content
                        elif entry.get("type") == "image":
                            # Store a placeholder for potentially enormous binary image data
                            input_messages.append({"content": "([IMAGE DETECTED])", "role": role})

                    if combined_text:
                        input_messages.append({"content": combined_text.strip(), "role": role})
                # Array of strings
                elif content and isinstance(content[0], str):
                    combined_text = " ".join(content)
                    input_messages.append({"content": combined_text, "role": role})
            # String content
            elif isinstance(content, str):
                input_messages.append({"content": content, "role": role})

        return input_messages

    @staticmethod
    def _extract_output_message(response):
        """Extract output messages from the stored response.
        Anthropic allows for chat messages, which requires some special casing.
        Bedrock's Converse API returns a different response format.
        """
        # Handle Converse API response format with output field structure
        if "output" in response and "message" in response.get("output", {}):
            message = response.get("output", {}).get("message", {})
            role = message.get("role", "assistant")
            tool_calls_info = []
            if message.get("content") and isinstance(message["content"], list):
                content_blocks = []
                for content_block in message["content"]:
                    if content_block.get("text") is not None:
                        content_blocks.append(content_block.get("text", ""))
                    elif content_block.get("toolUse") is not None:
                        tool_calls_info.append(
                            {
                                "name": content_block.get("toolUse", {}).get("name", ""),
                                "arguments": content_block.get("toolUse", {}).get("input", ""),
                                "tool_id": content_block.get("toolUse", {}).get("toolUseId", ""),
                            }
                        )
                    else:
                        log.debug(
                            "LLM Obs tracing is unsupported for content block type: %s", ",".join(content_block.keys())
                        )
                        content_blocks.append("[Unsupported content type: {}]".format(",".join(content_block.keys())))

                output_message = {"content": " ".join(content_blocks), "role": role}
                if tool_calls_info:
                    output_message["tool_calls"] = tool_calls_info
                return [output_message]

        # Standard InvokeModel API response format
        if isinstance(response.get("text", ""), str):
            return [{"content": response["text"]}]
        if isinstance(response.get("text", []), list):
            if isinstance(response["text"][0], str):
                return [{"content": str(content)} for content in response["text"]]
            if isinstance(response["text"][0], dict):
                return [{"content": response["text"][0].get("text", "")}]

        # Default empty response if format is not recognized
        return [{"content": ""}]
