from typing import Any
from typing import Optional

from ddtrace.contrib.internal.litellm._constants import DECISION_OPERATIONS
from ddtrace.contrib.internal.litellm._constants import ROUTER_INSTANCE_KEY
from ddtrace.contrib.internal.litellm._constants import SYSTEM_ONE_OPERATIONS
from ddtrace.contrib.internal.litellm._constants import TAG_RESPONSE_MODEL
from ddtrace.contrib.internal.litellm._constants import TEXT_COMPLETION_OPERATIONS
from ddtrace.contrib.internal.litellm._constants import model_map
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import IMAGE_DETECTED_MARKER
from ddtrace.llmobs._constants import IMAGE_FALLBACK_MARKER
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import REASONING_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.openai import openai_set_meta_tags_from_completion
from ddtrace.llmobs._integrations.utils import _capture_inline_image
from ddtrace.llmobs._integrations.utils import format_image_part_with_guard
from ddtrace.llmobs._integrations.utils import get_openrouter_cost_metrics
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_decision
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import ImagePart
from ddtrace.llmobs.types import Message
from ddtrace.trace import Span


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
SYSTEM_ONE_REQUEST_KEYS = ("model", "state", "questions", "images", "assets", "safety_identifier")
DECISION_PROVIDER_TAG = "decision_provider"
OPENAI_DECISION_PROVIDER = "openai"
SYSTEM_ONE_DECISION_PROVIDER = "typesafe"


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"
    # Shared with the contrib, which fills it in as litellm resolves providers.
    _model_map = model_map

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        """Nothing to do: the contrib sets the APM model/host tags itself."""

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        if operation in DECISION_OPERATIONS:
            self._llmobs_set_decision_tags(span, args, kwargs, response, operation)
            return
        model_name = get_argument_value(args, kwargs, 0, "model", False) or ""
        model_name, model_provider = self._model_map.get(model_name, (model_name, UNKNOWN_MODEL_PROVIDER))

        model_name = self._resolve_model_name(span, response, model_name, model_provider)

        span_kind = self._get_span_kind(span, kwargs, model_name, operation)
        metrics = self._extract_llmobs_metrics(response, span_kind)
        # Set kind before helpers so that input/output messages are routed correctly
        _annotate_llmobs_span_data(
            span, kind=span_kind, model_name=model_name or "", model_provider=model_provider, metrics=metrics
        )

        # use Open AI helpers since response format will match Open AI
        if operation in TEXT_COMPLETION_OPERATIONS:
            openai_set_meta_tags_from_completion(span, kwargs, response, integration_name="litellm")
        else:
            openai_set_meta_tags_from_chat(span, kwargs, response, integration_name="litellm")

        # custom logic for updating metadata on litellm spans
        self._update_litellm_metadata(span, kwargs, operation)

    def _llmobs_set_decision_tags(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any], operation: str
    ) -> None:
        model_name, model_provider = self._decision_model(args, kwargs, response)
        span_kind = self._get_span_kind(span, kwargs, model_name, operation)
        is_llm = span_kind == "llm"
        system_one = is_system_one_request(kwargs, operation)
        decision_provider = SYSTEM_ONE_DECISION_PROVIDER if system_one else OPENAI_DECISION_PROVIDER
        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            model_name=model_name,
            model_provider=model_provider,
            metrics=self._extract_decision_metrics(response) if is_llm else {},
            # The router span wraps the llm span of the same decision, so only the llm span carries it.
            tags={DECISION_PROVIDER_TAG: decision_provider} if is_llm else None,
        )
        if system_one:
            _set_system_one_io(span, kwargs, response)
        else:
            openai_set_meta_tags_from_decision(span, kwargs, response)
        self._update_litellm_metadata(span, kwargs, operation)

    def _decision_model(self, args: list[Any], kwargs: dict[str, Any], response: Optional[Any]) -> tuple[str, str]:
        """Model name and the provider litellm routed the call to, from the response when it has them."""
        requested = get_argument_value(args, kwargs, 0, "model", True) or ""
        hidden_params = getattr(response, "_hidden_params", None) or {}
        provider = hidden_params.get("custom_llm_provider") if isinstance(hidden_params, dict) else None
        model = hidden_params.get("model") if isinstance(hidden_params, dict) else None
        mapped_model, mapped_provider = self._model_map.get(requested, ("", ""))
        provider = provider or mapped_provider or kwargs.get("custom_llm_provider")
        if not provider and "/" in requested:
            provider = requested.split("/", 1)[0]
        model = model or mapped_model or requested
        if provider and isinstance(model, str) and model.startswith(f"{provider}/"):
            model = model[len(provider) + 1 :]
        return str(model or ""), str(provider or UNKNOWN_MODEL_PROVIDER)

    @staticmethod
    def _extract_decision_metrics(response: Any) -> dict[str, Any]:
        usage = _get_attr(response, "usage", None)
        if usage is None:
            return {}
        input_tokens = _get_attr(usage, "input_tokens", None)
        output_tokens = _get_attr(usage, "output_tokens", None)
        metrics: dict[str, Any] = {}
        if input_tokens is not None:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if input_tokens is not None or output_tokens is not None:
            total_tokens = _get_attr(usage, "total_tokens", None)
            metrics[TOTAL_TOKENS_METRIC_KEY] = total_tokens or (input_tokens or 0) + (output_tokens or 0)
        # OpenAI format usage keeps cache counts under input_tokens_details; System One format usage keeps them on itself.
        details = _get_attr(usage, "input_tokens_details", None) or usage
        cached_tokens = _get_attr(details, "cached_tokens", None)
        if cached_tokens:
            metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cached_tokens
        cache_write_tokens = _get_attr(details, "cache_write_tokens", None)
        if cache_write_tokens:
            metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_write_tokens
        metrics.update(get_openrouter_cost_metrics(usage))
        return metrics

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
            llm_router = kwargs.get(ROUTER_INSTANCE_KEY)
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

    def _resolve_model_name(self, span: Span, response: Optional[Any], model_name: str, model_provider: str) -> str:
        # Azure requires an arbitrary deployment name as the request model; the canonical model
        # comes from the response. azure_ai is excluded — LiteLLM rewrites its response model to
        # "azure_ai/<deployment>", which would not match the cost catalog.
        if model_provider in ("azure", "azure_text"):
            response_model = span.get_tag(TAG_RESPONSE_MODEL) or _get_attr(response, "model", None)
            if response_model:
                return str(response_model)
        return model_name

    def _set_apm_shadow_tags(self, span, args, kwargs, response=None, operation=""):
        if operation in DECISION_OPERATIONS:
            model_name, model_provider = self._decision_model(args, kwargs, response)
            span_kind = self._get_span_kind(span, kwargs, model_name, operation)
            metrics = self._extract_decision_metrics(response) if span_kind == "llm" else {}
            self._apply_shadow_metrics(span, metrics, span_kind, model_name=model_name, model_provider=model_provider)
            return
        model_name = get_argument_value(args, kwargs, 0, "model", False) or ""
        model_name, model_provider = self._model_map.get(model_name, (model_name, UNKNOWN_MODEL_PROVIDER))
        model_name = self._resolve_model_name(span, response, model_name, model_provider)
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

        metrics.update(get_openrouter_cost_metrics(token_usage))

        return metrics

    def _get_base_url(self, **kwargs: Any) -> Optional[str]:
        request_kwargs = kwargs.get("request_kwargs") or {}
        base_url = request_kwargs.get("base_url") or request_kwargs.get("api_base")
        return str(base_url) if base_url else None


def is_system_one_request(kwargs: dict[str, Any], operation: str) -> bool:
    """Whether the caller sent the System One body format: systemone, or the deprecated decisions(state=...)."""
    if operation in SYSTEM_ONE_OPERATIONS:
        return True
    return "state" in kwargs and kwargs.get("input") is None


def _set_system_one_io(span: Span, kwargs: dict[str, Any], response: Optional[Any]) -> None:
    """Record the System One request body as the input and the response body, without usage, as the output.

    Only known request fields are kept, since the other keyword arguments can carry credentials. Inline images become
    image parts and are replaced in the body by a marker. Audio and video clips are not recorded.
    """
    body = {k: kwargs[k] for k in SYSTEM_ONE_REQUEST_KEYS if kwargs.get(k) is not None}
    data = load_data_value(body)
    image_parts: list[ImagePart] = []
    if isinstance(data, dict):
        for key in ("images", "assets"):
            if key in data:
                data[key] = _system_one_media(data[key], image_parts)
    input_message = Message(role="user", content=safe_json(data) or "")
    if image_parts:
        input_message["image_parts"] = image_parts
    _annotate_llmobs_span_data(span, input_messages=[input_message])
    if not response:
        _annotate_llmobs_span_data(span, output_messages=[Message(content="")])
        return
    output = response.model_dump(mode="json") if hasattr(response, "model_dump") else load_data_value(response)
    if isinstance(output, dict):
        output.pop("usage", None)
    _annotate_llmobs_span_data(span, output_messages=[Message(role="assistant", content=safe_json(output) or "")])


def _system_one_media(value: Any, image_parts: list[ImagePart]) -> Any:
    """Replace inline image data (data URLs and base64 objects) with a marker; http(s) URLs are kept."""
    if isinstance(value, str):
        image_part, marker = _capture_inline_image(value)
        if image_part:
            image_parts.append(image_part)
            return IMAGE_DETECTED_MARKER
        return marker or value
    if isinstance(value, dict):
        if isinstance(value.get("base64"), str):
            mime_type = value.get("content_type") or value.get("media_type") or ""
            image_part = format_image_part_with_guard(value["base64"], mime_type)
            if image_part:
                image_parts.append(image_part)
                return IMAGE_DETECTED_MARKER
            return IMAGE_FALLBACK_MARKER
        return {k: _system_one_media(v, image_parts) for k, v in value.items()}
    if isinstance(value, list):
        return [_system_one_media(v, image_parts) for v in value]
    return value
