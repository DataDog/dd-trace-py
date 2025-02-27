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
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags
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
        parameters = {}
        if span.get_tag("bedrock.request.temperature"):
            parameters["temperature"] = float(span.get_tag("bedrock.request.temperature") or 0.0)
        if span.get_tag("bedrock.request.max_tokens"):
            parameters["max_tokens"] = int(span.get_tag("bedrock.request.max_tokens") or 0)

        prompt = kwargs.get("prompt", "")
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
                METADATA: parameters,
                METRICS: get_llmobs_metrics_tags("bedrock", span),
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
