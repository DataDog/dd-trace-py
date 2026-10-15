from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import REQUEST_BASE_URL
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import Message
from ddtrace.trace import Span


log = get_logger(__name__)

MODEL = "typesafe.request.model"
TYPESAFE_MODEL_PROVIDER = "typesafe"
OPENROUTER_MODEL_PROVIDER = "openrouter"


class TypeSafeIntegration(BaseLLMIntegration):
    _integration_name = "typesafe"

    def _set_base_span_tags(self, span: Span, model: Optional[str] = None, **kwargs: Any) -> None:
        base_url = self._get_base_url(**kwargs)
        if base_url is not None:
            span._set_ctx_item(REQUEST_BASE_URL, base_url)
        if model:
            span._set_attribute(MODEL, model)

    def _get_base_url(self, **kwargs: Any) -> Optional[str]:
        config = _get_attr(kwargs.get("instance"), "_config", None)
        base_url = _get_attr(config, "base_url", None)
        return str(base_url) if base_url else None

    def _get_model_provider(self, span: Span) -> str:
        base_url = span._get_ctx_item(REQUEST_BASE_URL) or ""
        # The SDK can be pointed at OpenRouter, which serves the same System One API
        if OPENROUTER_MODEL_PROVIDER in base_url.lower():
            return OPENROUTER_MODEL_PROVIDER
        return TYPESAFE_MODEL_PROVIDER

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_kind = "workflow" if span._get_ctx_item(PROXY_REQUEST) else "llm"
        state = kwargs.get("state")
        input_content = state if isinstance(state, str) else safe_json(state)  # type: ignore[no-untyped-call]
        input_messages = [Message(role="user", content=input_content or "")]
        metadata: dict[str, Any] = {}
        questions = kwargs.get("questions")
        if questions:
            metadata["questions"] = _to_data(questions)

        output_messages = [Message(content="")]
        metrics: dict[str, Any] = {}
        model_name = span.get_tag(MODEL) or ""
        if response is not None:
            output_content = safe_json(_to_data(response))  # type: ignore[no-untyped-call]
            output_messages = [Message(role="assistant", content=output_content or "")]
            model_name = _get_attr(response, "model", None) or model_name
            if span_kind == "llm":
                metrics = self._extract_usage(_get_attr(response, "usage", None))

        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            model_name=model_name,
            model_provider=self._get_model_provider(span),
            input_messages=input_messages,
            output_messages=output_messages,
            metadata=metadata,
            metrics=metrics,
        )

    def _set_apm_shadow_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_kind = "workflow" if span._get_ctx_item(PROXY_REQUEST) else "llm"
        metrics = self._extract_usage(_get_attr(response, "usage", None)) if span_kind == "llm" else {}
        model_name = _get_attr(response, "model", None) or span.get_tag(MODEL)
        self._apply_shadow_metrics(
            span, metrics, span_kind, model_name=model_name, model_provider=self._get_model_provider(span)
        )

    @staticmethod
    def _extract_usage(usage: Any) -> dict[str, Any]:
        input_tokens = _get_attr(usage, "input_tokens", None)
        output_tokens = _get_attr(usage, "output_tokens", None)
        metrics: dict[str, Any] = {}
        if input_tokens is not None:
            metrics[INPUT_TOKENS_METRIC_KEY] = input_tokens
        if output_tokens is not None:
            metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
        if input_tokens is not None or output_tokens is not None:
            metrics[TOTAL_TOKENS_METRIC_KEY] = (input_tokens or 0) + (output_tokens or 0)
        return metrics


def _to_data(value: Any) -> Any:
    """Convert SDK objects to JSON-compatible data.

    typesafe-sdk 0.6 models are msgspec Structs, which load_data_value does not recognize; later versions use pydantic.
    """
    if isinstance(value, dict):
        return {str(k): _to_data(v) for k, v in value.items()}
    struct_fields = getattr(type(value), "__struct_fields__", None)
    if struct_fields is not None:
        data = {f: _to_data(getattr(value, f)) for f in struct_fields if getattr(value, f, None) is not None}
        # A tagged Struct keeps its discriminator (e.g. "type") in its config, not in its fields
        struct_config = getattr(value, "__struct_config__", None)
        tag_field = getattr(struct_config, "tag_field", None)
        if tag_field:
            data[tag_field] = getattr(struct_config, "tag", None)
        return data
    return load_data_value(value)  # type: ignore[no-untyped-call]
