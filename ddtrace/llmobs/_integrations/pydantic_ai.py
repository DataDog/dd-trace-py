from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span



class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"

    def _set_base_span_tags(
        self, span: Span, model: Optional[str] = None
    ) -> None:
        if model:
            span.set_tag("pydantic_ai.request.model", getattr(model, "model_name", ""))
            provider = getattr(model, "_provider", None)
            system = getattr(model, "system", None)
            # different model providers have different model classes and ways of accessing the provider name
            if provider or system:
                span.set_tag("pydantic_ai.request.provider", getattr(provider, "name", "") or system)

    