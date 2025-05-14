from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_completion
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._utils import _get_attr
from ddtrace.trace import Span


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"
    # maps requested model name to parsed model name and provider
    _model_map: Dict[str, Tuple[str, str]] = {}

    def _set_base_span_tags(
        self, span: Span, model: Optional[str] = None, host: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if model is not None:
            span.set_tag_str("litellm.request.model", model)
        if host is not None:
            span.set_tag_str("litellm.request.host", host)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        model_name = get_argument_value(args, kwargs, 0, "model", False) or ""
        model_name, model_provider = self._model_map.get(model_name, (model_name, ""))

        # use Open AI helpers since response format will match Open AI
        if operation == "completion":
            openai_set_meta_tags_from_completion(span, kwargs, response)
        else:
            openai_set_meta_tags_from_chat(span, kwargs, response)

        metrics = self._extract_llmobs_metrics(response)
        base_url = kwargs.get("api_base", None)
        span._set_ctx_items(
            {SPAN_KIND: "llm" if not base_url else "workflow", MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider, METRICS: metrics}
        )

    @staticmethod
    def _extract_llmobs_metrics(resp: Any) -> Dict[str, Any]:
        if not resp:
            return {}
        if isinstance(resp, list):
            token_usage = _get_attr(resp[0], "usage", None)
        else:
            token_usage = _get_attr(resp, "usage", None)
        if token_usage is None:
            return {}
        prompt_tokens = _get_attr(token_usage, "prompt_tokens", 0)
        completion_tokens = _get_attr(token_usage, "completion_tokens", 0)
        return {
            INPUT_TOKENS_METRIC_KEY: prompt_tokens,
            OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
            TOTAL_TOKENS_METRIC_KEY: prompt_tokens + completion_tokens,
        }

    def should_submit_to_llmobs(self, kwargs: Dict[str, Any], model: Optional[str] = None) -> bool:
        """
        Span should be NOT submitted to LLMObs if:
            - non-streamed request and model provider is OpenAI/AzureOpenAI and the OpenAI integration
                is enabled: this request will be captured in the OpenAI integration instead
        """
        stream = kwargs.get("stream", False)
        model_lower = model.lower() if model else ""
        # model provider is unknown until request completes; therefore, this is a best effort attempt to check
        # if model provider is Open AI or Azure
        if (
            any(prefix in model_lower for prefix in ("gpt", "openai", "azure"))
            and not stream
            and LLMObs._integration_is_enabled("openai")
        ):
            return False
        return True
