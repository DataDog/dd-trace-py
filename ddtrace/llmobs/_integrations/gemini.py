from typing import Any
from typing import Dict
from typing import Optional

from ddtrace import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class GeminiIntegration(BaseLLMIntegration):
    _integration_name = "gemini"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_generativeai.request.provider", str(provider))
        if model is not None:
            span.set_tag_str("google_generativeai.request.model", str(model))
