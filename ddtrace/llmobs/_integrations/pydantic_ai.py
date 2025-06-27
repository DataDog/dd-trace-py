from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._utils import _get_attr


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
    
    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        metrics = self.extract_usage_metrics(response)
        span._set_ctx_items(
            {SPAN_KIND: span._get_ctx_item(SPAN_KIND), MODEL_NAME: span.get_tag("pydantic.request.model") or "", MODEL_PROVIDER: span.get_tag("pydantic.request.model"), METRICS: metrics}
        )
    
    def extract_usage_metrics(self, response: Any) -> Dict[str, Any]:
        usage = None
        try:
            usage = response.usage()
        except Exception:
            return {}
        if usage is None:
            return {}

        prompt_tokens = _get_attr(usage, "request_tokens", 0)
        completion_tokens = _get_attr(usage, "response_tokens", 0)
        total_tokens = _get_attr(usage, "total_tokens", 0)
        return {
            INPUT_TOKENS_METRIC_KEY: prompt_tokens,
            OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
            TOTAL_TOKENS_METRIC_KEY: total_tokens or (prompt_tokens + completion_tokens),
        }
        