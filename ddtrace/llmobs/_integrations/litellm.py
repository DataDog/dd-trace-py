from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.trace import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"
    _provider_map = {}

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("litellm.request.provider", provider)
        if model is not None:
            span.set_tag_str("litellm.request.model", model)