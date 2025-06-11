from typing import Any, Dict, List, Optional
from ddtrace._trace.span import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration

# from ddtrace.llmobs._constants import INPUT_MESSAGES
# from ddtrace.llmobs._constants import METADATA
# from ddtrace.llmobs._constants import METRICS
# from ddtrace.llmobs._constants import MODEL_NAME
# from ddtrace.llmobs._constants import MODEL_PROVIDER
# from ddtrace.llmobs._constants import OUTPUT_MESSAGES
# from ddtrace.llmobs._constants import SPAN_KIND

class GoogleGenAIIntegration(BaseLLMIntegration):
    _integration_name = "google_genai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_genai.request.provider", provider)
        if model is not None:
            span.set_tag_str("google_genai.request.model", model)