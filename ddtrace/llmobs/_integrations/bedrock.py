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
        """Extract prompt/response attributes from an execution context.

        ctx is a require argument is of the shape:
        {
            "resource": str, # oneof("Converse", "InvokeModel")
            "model_name": str,
            "model_provider": str,
            "request_params": {"prompt": str | list[dict],
                                "temperature": Optional[float],
                                "max_tokens": Optional[int]
                                "top_p": Optional[int]}
            "usage": Optional[dict],
            "stop_reason": Optional[str],
        }
        """
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

        if "total_tokens" not in usage_metrics and (
            "input_tokens" in usage_metrics or "output_tokens" in usage_metrics
        ):
            usage_metrics["total_tokens"] = usage_metrics.get("input_tokens", 0) + usage_metrics.get("output_tokens", 0)

        if "temperature" in request_params and request_params.get("temperature") != "":
            metadata["temperature"] = float(request_params.get("temperature") or 0.0)
        if "max_tokens" in request_params and request_params.get("max_tokens") != "":
            metadata["max_tokens"] = int(request_params.get("max_tokens") or 0)
        if "top_p" in request_params and request_params.get("top_p") != "":
            metadata["top_p"] = float(request_params.get("top_p") or 0.0)

        prompt = request_params.get("prompt", "")

        input_messages = (
            self._extract_input_message_for_converse(prompt)
            if ctx["resource"] == "Converse"
            else self._extract_input_message(prompt)
        )

        output_messages = [{"content": ""}]
        if not span.error and response is not None:
            output_messages = (
                self._extract_output_message_for_converse(response)
                if ctx["resource"] == "Converse"
                else self._extract_output_message(response)
            )
        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: ctx.get_item("model_name") or "",
                MODEL_PROVIDER: ctx.get_item("model_provider") or "",
                INPUT_MESSAGES: input_messages,
                METADATA: metadata,
                METRICS: usage_metrics,
                OUTPUT_MESSAGES: output_messages,
            }
        )

    @staticmethod
    def _extract_input_message_for_converse(prompt: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
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
                if content and isinstance(content[0], dict):
                    combined_text = ""
                    for entry in content:
                        if entry.get("text"):
                            combined_text += entry.get("text", "") + " "
                        else:
                            content_type = ",".join(entry.keys())
                            input_messages.append(
                                {"content": "[Unsupported content type: {}]".format(content_type), "role": role}
                            )
                    if combined_text:
                        input_messages.append({"content": combined_text.strip(), "role": role})

        return input_messages

    @staticmethod
    def _extract_output_message_for_converse(response: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        return [{"content": ""}]

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
            content = p.get("content", "")
            if isinstance(content, list) and isinstance(content[0], dict):
                for entry in content:
                    if entry.get("type") == "text":
                        input_messages.append({"content": entry.get("text", ""), "role": str(p.get("role", ""))})
                    elif entry.get("type") == "image":
                        # Store a placeholder for potentially enormous binary image data.
                        input_messages.append({"content": "([IMAGE DETECTED])", "role": str(p.get("role", ""))})
            else:
                input_messages.append({"content": content, "role": str(p.get("role", ""))})
        return input_messages

    @staticmethod
    def _extract_output_message(response):
        """Extract output messages from the stored response.
        Anthropic allows for chat messages, which requires some special casing.
        """
        if isinstance(response["text"], str):
            return [{"content": response["text"]}]
        if isinstance(response["text"], list):
            if isinstance(response["text"][0], str):
                return [{"content": str(content)} for content in response["text"]]
            if isinstance(response["text"][0], dict):
                return [{"content": response["text"][0].get("text", "")}]
