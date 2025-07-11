from typing import Optional

from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"

    def _set_base_span_tags(self, span: Span, model: Optional[str] = None, **kwargs) -> None:
        if model:
            span.set_tag("pydantic_ai.request.model", getattr(model, "model_name", ""))
            system = getattr(model, "system", None)
            if system:
                system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
                span.set_tag("pydantic_ai.request.provider", system)
