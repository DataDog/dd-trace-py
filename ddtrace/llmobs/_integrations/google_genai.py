from typing import Any, Dict, List, Optional
from ddtrace._trace.span import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration

from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND

class GoogleGenAIIntegration(BaseLLMIntegration):
    _integration_name = "google_genai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_genai.request.provider", provider)
        if model is not None:
            span.set_tag_str("google_genai.request.model", model)

    def _llmobs_set_tags(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Any | None = None, operation: str = "") -> None:
        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("google_genai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("google_genai.request.provider") or "",
                # METADATA: metadata,
                # INPUT_MESSAGES: input_messages,
                # OUTPUT_MESSAGES: output_messages,
                # METRICS: get_llmobs_metrics_tags("vertexai", span),
            }
        )