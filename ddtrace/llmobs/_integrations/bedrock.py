import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations import BaseLLMIntegration


log = get_logger(__name__)


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def llmobs_set_tags(
        self,
        span: Span,
        formatted_response: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        err: bool = False,
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.*" tags."""
        if not self.llmobs_enabled:
            return
        parameters = {"temperature": float(span.get_tag("bedrock.request.temperature") or 0.0)}
        max_tokens = int(span.get_tag("bedrock.request.max_tokens") or 0)
        if max_tokens:
            parameters["max_tokens"] = max_tokens
        input_messages = self._extract_input_message(prompt)

        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("bedrock.request.model") or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag("bedrock.request.model_provider") or "")
        span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages))
        span.set_tag_str(INPUT_PARAMETERS, json.dumps(parameters))
        if err or formatted_response is None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
        else:
            output_messages = self._extract_output_message(formatted_response)
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages))
        span.set_tag_str(METRICS, json.dumps(self._llmobs_metrics(span, formatted_response)))

    @staticmethod
    def _llmobs_metrics(span: Span, formatted_response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        metrics = {}
        if formatted_response and formatted_response.get("text"):
            prompt_tokens = int(span.get_tag("bedrock.usage.prompt_tokens") or 0)
            completion_tokens = int(span.get_tag("bedrock.usage.completion_tokens") or 0)
            metrics["prompt_tokens"] = prompt_tokens
            metrics["completion_tokens"] = completion_tokens
            metrics["total_tokens"] = prompt_tokens + completion_tokens
        return metrics

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
    def _extract_output_message(formatted_response):
        """Extract output messages from the stored response.
        Anthropic allows for chat messages, which requires some special casing.
        """
        if isinstance(formatted_response["text"], str):
            return [{"content": formatted_response["text"]}]
        if isinstance(formatted_response["text"], list):
            if isinstance(formatted_response["text"][0], str):
                return [{"content": str(resp)} for resp in formatted_response["text"]]
            if isinstance(formatted_response["text"][0], dict):
                return [{"content": formatted_response["text"][0].get("text", "")}]
