from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.trace import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"
    _provider_map = {}

    def _set_base_span_tags(
        self, span: Span, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if model is not None:
            span.set_tag_str("litellm.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        model_name = span.get_tag("litellm.request.model")
        model_provider = self._provider_map.get(model_name, "")

        span._set_ctx_items(
            {SPAN_KIND: "llm", MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider}
        )

    def should_submit_to_llmobs(self, base_url: Optional[str] = None) -> bool:
        return base_url is None
