from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY, METRICS, OUTPUT_TOKENS_METRIC_KEY, TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags, openai_set_meta_tags_from_chat, openai_set_meta_tags_from_completion
from ddtrace.llmobs._utils import _get_attr
from ddtrace.trace import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"
    # maps requested model name to parsed model name and provider
    _model_map = {}

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
        _, model_provider = self._model_map.get(model_name, (model_name, ""))

        # response format will match Open AI
        if operation == "completion":
            openai_set_meta_tags_from_completion(span, kwargs, response)
        else:
            openai_set_meta_tags_from_chat(span, kwargs, response)

        metrics = self._extract_llmobs_metrics(response)
        span._set_ctx_items(
            {SPAN_KIND: "llm", MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider, METRICS: metrics}
        )

    @staticmethod
    def _extract_llmobs_metrics(resp: Any) -> Dict[str, Any]:
        if isinstance(resp, list):
            token_usage = _get_attr(resp[0], "usage", None)
        else:
            token_usage = _get_attr(resp, "usage", None)
        if token_usage is not None:
            prompt_tokens = _get_attr(token_usage, "prompt_tokens", 0)
            completion_tokens = _get_attr(token_usage, "completion_tokens", 0)
            return {
                INPUT_TOKENS_METRIC_KEY: prompt_tokens,
                OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
                TOTAL_TOKENS_METRIC_KEY: prompt_tokens + completion_tokens,
            }

    def should_submit_to_llmobs(self, base_url: Optional[str] = None) -> bool:
        return base_url is None
