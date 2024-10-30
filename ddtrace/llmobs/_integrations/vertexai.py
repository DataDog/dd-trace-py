
from typing import Optional, Dict, Any

from ddtrace import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class VertexAIIntegration(BaseLLMIntegration):
    _integration_name = "vertexai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("vertexai.request.provider", str(provider))
        if model is not None:
            span.set_tag_str("vertexai.request.model", str(model))

    