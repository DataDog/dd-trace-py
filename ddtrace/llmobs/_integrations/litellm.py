from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_completion
from ddtrace.llmobs._integrations.utils import update_proxy_workflow_input_output_value
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._utils import _get_attr
from ddtrace.trace import Span


TEXT_COMPLETION_OPERATIONS = (
    "text_completion",
    "atext_completion",
    "router.text_completion",
    "router.atext_completion",
)

MODEL_LIST_KEYS = ("api_base", "model", "organization", "max_tokens", "temperature", "tags")
METADATA_KEYS = (
    "deployment",
    "endpoint",
    "headers",
    "litellm_api_version",
    "model_group",
    "model_group_size",
    "model_info",
    "tags",
)
PROXY_SERVER_REQUEST_KEYS = ("body", "url", "method")


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
        if self.is_completion_operation(operation):
            openai_set_meta_tags_from_completion(span, kwargs, response, integration_name="litellm")
        else:
            openai_set_meta_tags_from_chat(span, kwargs, response, integration_name="litellm")

        # custom logic for updating metadata on litellm spans
        self._update_litellm_metadata(span, kwargs, operation)

        # update input and output value for non-LLM spans
        span_kind = self._get_span_kind(span, kwargs, model_name, operation)
        update_proxy_workflow_input_output_value(span, span_kind)

        metrics = self._extract_llmobs_metrics(response, span_kind)
        span._set_ctx_items(
            {SPAN_KIND: span_kind, MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider, METRICS: metrics}
        )

    def _update_litellm_metadata(self, span: Span, kwargs: Dict[str, Any], operation: str):
        metadata = span._get_ctx_item(METADATA) or {}
        base_url = kwargs.get("base_url") or kwargs.get("api_base")
        # select certain keys within metadata to avoid sending sensitive data
        if "metadata" in metadata:
            inner_metadata = {}
            for key in METADATA_KEYS:
                value = metadata["metadata"].get(key)
                if key == "headers" and value:
                    value = {"host": value.get("host")}
                if value:
                    inner_metadata[key] = value
            metadata["metadata"] = inner_metadata
        if "proxy_server_request" in metadata:
            metadata["proxy_server_request"] = self._select_keys(
                metadata["proxy_server_request"], PROXY_SERVER_REQUEST_KEYS
            )

        if base_url and "model" in kwargs:
            metadata["model"] = kwargs["model"]
            span._set_ctx_items({METADATA: metadata})
            return
        if base_url or "router" not in operation:
            span._set_ctx_items({METADATA: metadata})
            return

        llm_router = kwargs.get(LITELLM_ROUTER_INSTANCE_KEY)
        if not llm_router:
            span._set_ctx_items({METADATA: metadata})
            return

        metadata["router_settings"] = {
            "router_general_settings": getattr(llm_router, "router_general_settings", None),
            "routing_strategy": getattr(llm_router, "routing_strategy", None),
            "routing_strategy_args": getattr(llm_router, "routing_strategy_args", None),
            "provider_budget_config": getattr(llm_router, "provider_budget_config", None),
            "retry_policy": getattr(llm_router, "retry_policy", None),
            "enable_tag_filtering": getattr(llm_router, "enable_tag_filtering", None),
        }
        if hasattr(llm_router, "get_model_list"):
            metadata["router_settings"]["model_list"] = self._construct_litellm_model_list(llm_router.get_model_list())

        span._set_ctx_items({METADATA: metadata})

    def _construct_litellm_model_list(self, model_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        new_model_list = []
        for model in model_list:
            litellm_params = model.get("litellm_params", {})
            litellm_params_dict = self._select_keys(litellm_params, MODEL_LIST_KEYS)
            new_model_list.append(
                {
                    "model_name": model.get("model_name"),
                    "litellm_params": litellm_params_dict,
                }
            )
        return new_model_list

    def _has_downstream_openai_span(self, kwargs: Dict[str, Any], model: Optional[str] = None) -> bool:
        """
        Determine whether an LLM span will be submitted for the given request from outside the LiteLLM integration.

        Currently, this only applies to the OpenAI integration when the following conditions are met:
            - request is not streamed
            - model provider is OpenAI/AzureOpenAI
            - the OpenAI integration is enabled
        """
        stream = kwargs.get("stream", False)
        model_lower = model.lower() if model else ""
        # best effort attempt to check if Open AI or Azure since model_provider is unknown until request completes
        is_openai_model = any(prefix in model_lower for prefix in ("gpt", "openai", "azure"))
        return is_openai_model and not stream and LLMObs._integration_is_enabled("openai")

    def _get_span_kind(
        self, span: Span, kwargs: Dict[str, Any], model: Optional[str] = None, operation: Optional[str] = None
    ) -> str:
        """
        Workflow span should be submitted to LLMObs if:
            - span represents a router operation OR
            - span represents a proxy request
        LLM spans should be submitted to LLMObs if:
            - span does not represent a router operation or a proxy request
        """
        if self.is_router_operation(operation) or span._get_ctx_item(PROXY_REQUEST):
            return "workflow"
        return "llm"

    def _select_keys(self, data: Dict[str, Any], keys_to_select: Tuple[str, ...]) -> Dict[str, Any]:
        new_data = {}
        for key in keys_to_select:
            value = data.get(key)
            if value:
                new_data[key] = value
        return new_data

    def is_completion_operation(self, operation: str) -> bool:
        return operation in TEXT_COMPLETION_OPERATIONS

    def is_router_operation(self, operation: Optional[str]) -> bool:
        return "router" in operation if operation else False

    @staticmethod
    def _extract_llmobs_metrics(resp: Any, span_kind: str) -> Dict[str, Any]:
        if not resp or span_kind != "llm":
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

    def _get_base_url(self, **kwargs: Dict[str, Any]) -> Optional[str]:
        base_url = kwargs.get("base_url")
        return str(base_url) if base_url else None
