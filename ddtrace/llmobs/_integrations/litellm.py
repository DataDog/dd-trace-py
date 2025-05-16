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
from ddtrace.llmobs._constants import METADATA
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
        
        # custom logic for updating metadata on litellm spans
        self._update_litellm_metadata(span, kwargs)

        metrics = self._extract_llmobs_metrics(response)
        span._set_ctx_items(
            {SPAN_KIND: self._get_span_kind(kwargs, model_name), MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider, METRICS: metrics}
        )
    
    def _update_litellm_metadata(self, span: Span, kwargs: Dict[str, Any]):
        metadata = span._get_ctx_item(METADATA)
        base_url = kwargs.get("api_base", None)
        # only add model to metadata if it's a litellm client request
        if base_url:
            if "model" in kwargs:
                metadata["model"] = kwargs["model"]
        # add proxy information to metadata if it's a span originating from the proxy
        else:
            if "metadata" in metadata and "user_api_key" in metadata["metadata"]:
                del metadata["metadata"]["user_api_key"]

            if not kwargs.get("router_instance", None):
                span._set_ctx_items({METADATA: metadata})
                return

            routing_info = {}
            llm_router = kwargs["router_instance"]
            routing_info["router_general_settings"] = getattr(llm_router, "router_general_settings", None)
            routing_info["routing_strategy"] = getattr(llm_router, "routing_strategy", None)
            routing_info["routing_strategy_args"] = getattr(llm_router, "routing_strategy_args", None)
            routing_info["provider_budget_config"] = getattr(llm_router, "provider_budget_config", None)
            routing_info["retry_policy"] = getattr(llm_router, "retry_policy", None)
            model_list = llm_router.get_model_list() if hasattr(llm_router, "get_model_list") else []
            routing_info["model_list"] = self._scrub_litellm_model_list(model_list)
            metadata["router_settings"] = routing_info

        span._set_ctx_items({METADATA: metadata})

    def _scrub_litellm_model_list(self, model_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for model in model_list:
            if "litellm_params" in model and "api_key" in model["litellm_params"]:
                del model["litellm_params"]["api_key"]
        return model_list

    def _skip_llm_span(self, kwargs: Dict[str, Any], model: Optional[str] = None) -> bool:
        """
        Determine whether an LLM span will be submitted for the given request from outside the LiteLLM integration.
        
        Currently, this only applies to the OpenAI integration when the following conditions are met:
            - request is not streamed
            - model provider is OpenAI/AzureOpenAI
            - the OpenAI integration is enabled
        """
        stream = kwargs.get("stream", False)
        model_lower = model.lower() if model else ""
        # model provider is unknown until request completes; therefore, this is a best effort attempt to check
        # if model provider is Open AI or Azure
        return (
            any(prefix in model_lower for prefix in ("gpt", "openai", "azure"))
            and not stream
            and LLMObs._integration_is_enabled("openai")
        )

    def _get_span_kind(self, kwargs: Dict[str, Any], model: Optional[str] = None) -> str:
        """
        Workflow span should be submitted to LLMObs if:
            - base_url is set (indicates a request to the proxy) OR
            - base_url is not set AND an LLM span will be submitted elsewhere
        LLM spans should be submitted to LLMObs if:
            - base_url is not set AND an LLM span will not be submitted elsewhere
        """
        base_url = kwargs.get("api_base", None)
        if base_url or self._skip_llm_span(kwargs, model):
            return "workflow"
        return "llm"

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
        LiteLLM spans will be submitted to LLMObs unless the following are true:
            - base_url is not set
            - the LLM request will be submitted elsewhere (e.g. OpenAI integration)
        """
        base_url = kwargs.get("api_base", None)
        if not base_url and not self._skip_llm_span(kwargs, model):
            return False
        return True
