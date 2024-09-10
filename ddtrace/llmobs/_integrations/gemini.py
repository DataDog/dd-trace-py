from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace import Span


class GeminiIntegration(BaseLLMIntegration):
    _integration_name = "gemini"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider:
            span.set_tag_str("genai.request.model", model)
        if model:
            span.set_tag_str("genai.request.provider", provider)
