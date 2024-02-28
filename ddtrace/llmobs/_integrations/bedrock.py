import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations import BaseLLMIntegration


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def llmobs_set_tags(
        self,
        span: Span,
        formatted_response: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        err: bool = False,
    ) -> None:
        if not self.llmobs_enabled:
            return
        parameters = {
            "temperature": float(span.get_tag("bedrock.request.temperature") or 0.0),
            "max_tokens": int(span.get_tag("bedrock.request.max_tokens") or 0),
        }
        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag("bedrock.request.model") or "")
        span.set_tag_str(MODEL_PROVIDER, span.get_tag("bedrock.request.model_provider") or "")
        span.set_tag_str(INPUT_MESSAGES, json.dumps([{"content": prompt}]))
        span.set_tag_str(INPUT_PARAMETERS, json.dumps(parameters))
        if err or formatted_response is None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
        else:
            span.set_tag_str(
                OUTPUT_MESSAGES, json.dumps([{"content": completion} for completion in formatted_response["text"]])
            )
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
