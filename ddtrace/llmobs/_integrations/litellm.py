from typing import Any
from typing import Optional

from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import REASONING_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_completion
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import get_llmobs_metadata
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
    _model_map: dict[str, tuple[str, str]] = {}

    def _set_base_span_tags(
        self, span: Span, model: Optional[str] = None, host: Optional[str] = None, **kwargs: dict[str, Any]
    ) -> None:
        if model is not None:
            span._set_attribute("litellm.request.model", model)
        if host is not None:
            span._set_attribute("litellm.request.host", host)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        model_name = get_argument_value(args, kwargs, 0, "model", False) or ""
        model_name, model_provider = self._model_map.get(model_name, (model_name, UNKNOWN_MODEL_PROVIDER))

        span_kind = self._get_span_kind(span, kwargs, model_name, operation)
        metrics = self._extract_llmobs_metrics(response, span_kind)
        # Set kind before helpers so that input/output messages are routed correctly
        _annotate_llmobs_span_data(
            span, kind=span_kind, model_name=model_name or "", model_provider=model_provider, metrics=metrics
        )

        # use Open AI helpers since response format will match Open AI
        if self.is_completion_operation(operation):
            openai_set_meta_tags_from_completion(span, kwargs, response, integration_name="litellm")
        else:
            openai_set_meta_tags_from_chat(span, kwargs, response, integration_name="litellm")

        # custom logic for updating metadata on litellm spans
        self._update_litellm_metadata(span, kwargs, operation)

    def _update_litellm_metadata(self, span: Span, kwargs: dict[str, Any], operation: str):
        metadata = get_llmobs_metadata(span) or {}
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
        elif not base_url and "router" in operation:
            llm_router = kwargs.get(LITELLM_ROUTER_INSTANCE_KEY)
            if llm_router:
                metadata["router_settings"] = {
                    "router_general_settings": getattr(llm_router, "router_general_settings", None),
                    "routing_strategy": getattr(llm_router, "routing_strategy", None),
                    "routing_strategy_args": getattr(llm_router, "routing_strategy_args", None),
                    "provider_budget_config": getattr(llm_router, "provider_budget_config", None),
                    "retry_policy": getattr(llm_router, "retry_policy", None),
                    "enable_tag_filtering": getattr(llm_router, "enable_tag_filtering", None),
                }
                if hasattr(llm_router, "get_model_list"):
                    metadata["router_settings"]["model_list"] = self._construct_litellm_model_list(
                        llm_router.get_model_list()
                    )

        _annotate_llmobs_span_data(span, metadata=metadata)

    def _construct_litellm_model_list(self, model_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    def _has_downstream_openai_span(self, kwargs: dict[str, Any], model: Optional[str] = None) -> bool:
        """
        Determine whether an LLM span will be submitted for the given request from outside the LiteLLM integration.

        Currently, this only applies to the OpenAI integration when the following conditions are met:
            - request is not streamed
            - model provider is OpenAI/AzureOpenAI
            - the OpenAI integration is enabled
        """
        stream = kwargs.get("stream", False)
        model_lower = model.lower() if model else ""
        # Detect litellm proxy routing — the OpenAI integration never fires when requests go through a proxy.
        # Three mechanisms exist (v1.72.1+ for use_litellm_proxy):
        #   1. model prefix:        model="litellm_proxy/<model>"
        #   2. per-call kwarg:      use_litellm_proxy=True
        #   3. module-level flag:   litellm.use_litellm_proxy = True  /  USE_LITELLM_PROXY env var
        if model_lower.startswith("litellm_proxy/"):
            return False
        if kwargs.get("use_litellm_proxy"):
            return False
        import litellm as _litellm

        if getattr(_litellm, "use_litellm_proxy", False) or env.get("USE_LITELLM_PROXY", "").lower() == "true":
            return False
        # best effort attempt to check if Open AI or Azure since model_provider is unknown until request completes
        is_openai_model = any(prefix in model_lower for prefix in ("gpt", "openai", "azure"))
        return is_openai_model and not stream and LLMObs._integration_is_enabled("openai")

    def _set_apm_shadow_tags(self, span, args, kwargs, response=None, operation=""):
        model_name = get_argument_value(args, kwargs, 0, "model", False) or ""
        model_name, model_provider = self._model_map.get(model_name, (model_name, UNKNOWN_MODEL_PROVIDER))
        span_kind = self._get_span_kind(span, kwargs, model_name, operation)
        metrics = self._extract_llmobs_metrics(response, span_kind)
        self._apply_shadow_metrics(
            span,
            metrics,
            span_kind,
            model_name=model_name,
            model_provider=model_provider,
        )

    def _get_span_kind(
        self, span: Span, kwargs: dict[str, Any], model: Optional[str] = None, operation: Optional[str] = None
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

    def _select_keys(self, data: dict[str, Any], keys_to_select: tuple[str, ...]) -> dict[str, Any]:
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
    def _extract_llmobs_metrics(resp: Any, span_kind: str) -> dict[str, Any]:
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

        metrics: dict[str, Any] = {
            INPUT_TOKENS_METRIC_KEY: prompt_tokens,
            OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
            TOTAL_TOKENS_METRIC_KEY: prompt_tokens + completion_tokens,
        }
        completion_tokens_details = _get_attr(token_usage, "completion_tokens_details", {})
        if completion_tokens_details:
            reasoning_tokens = _get_attr(completion_tokens_details, "reasoning_tokens", None)
            if reasoning_tokens is not None:
                metrics[REASONING_OUTPUT_TOKENS_METRIC_KEY] = reasoning_tokens

        # Extract cache read tokens from litellm's normalized prompt_tokens_details
        prompt_tokens_details = _get_attr(token_usage, "prompt_tokens_details", None)
        if prompt_tokens_details is not None:
            cached_tokens = _get_attr(prompt_tokens_details, "cached_tokens", None)
            if cached_tokens:
                metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cached_tokens
            # cache_creation_tokens + cache TTL breakdown is on prompt_tokens_details in litellm >=1.77.3
            cache_creation_tokens = _get_attr(prompt_tokens_details, "cache_creation_tokens", None)
            if cache_creation_tokens:
                metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_creation_tokens
                cache_creation_token_details = _get_attr(prompt_tokens_details, "cache_creation_token_details", None)
                if cache_creation_token_details is not None:
                    ephemeral_1h = _get_attr(cache_creation_token_details, "ephemeral_1h_input_tokens", None)
                    ephemeral_5m = _get_attr(cache_creation_token_details, "ephemeral_5m_input_tokens", None)
                    metrics[CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY] = ephemeral_1h or 0
                    metrics[CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY] = ephemeral_5m or 0
                else:
                    # if no cache write TTL breakdown available, assume all writes are 5m TTL
                    metrics[CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY] = 0
                    metrics[CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY] = cache_creation_tokens

        # Fallback: cache_creation_input_tokens on usage object (litellm 1.65.4)
        if CACHE_WRITE_INPUT_TOKENS_METRIC_KEY not in metrics:
            cache_creation_tokens = _get_attr(token_usage, "cache_creation_input_tokens", None)
            if cache_creation_tokens:
                metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_creation_tokens
                # if no cache write TTL breakdown available, assume all writes are 5m TTL
                metrics[CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY] = 0
                metrics[CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY] = cache_creation_tokens

        return metrics

    def _get_base_url(self, **kwargs: dict[str, Any]) -> Optional[str]:
        base_url = kwargs.get("base_url")
        return str(base_url) if base_url else None
