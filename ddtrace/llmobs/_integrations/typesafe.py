from collections.abc import Mapping
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
        input_content = safe_json(_request_body(kwargs))  # type: ignore[no-untyped-call]
        input_messages = [Message(role="user", content=input_content or "")]

        output_messages = [Message(content="")]
        metrics: dict[str, Any] = {}
        model_name = span.get_tag(MODEL) or ""
        if response is not None:
            output_messages = [Message(role="assistant", content=_response_body(response))]
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


def _request_body(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the JSON body that system_one sends.

    Images (typesafe-sdk 0.7.5+) appear as their alias from assets= or their <image:...> reference, as on the wire,
    and are listed under "assets" in the order the SDK uploads them; their bytes go in separate parts and are not
    recorded.
    """
    aliases = {id(asset): name for name, asset in (kwargs.get("assets") or {}).items()}
    found: dict[str, None] = {}
    body = {"state": kwargs.get("state"), "model": kwargs.get("model"), "questions": kwargs.get("questions")}
    body.update(kwargs.get("extra_body") or {})
    data: dict[str, Any] = _to_data(body, aliases, found)
    if "assets" not in body:
        names = list(dict.fromkeys([*aliases.values(), *found]))
        if names:
            data["assets"] = {name: {"type": "image", "part": f"asset-{i}"} for i, name in enumerate(names)}
    return data


def _response_body(response: Any) -> str:
    """Return the HTTP response body as received, or the parsed response when the raw one is unavailable."""
    try:
        raw_text = response.raw_http_response.text
    except Exception:
        raw_text = None
    if isinstance(raw_text, str) and raw_text:
        return raw_text
    return safe_json(_to_data(response)) or ""  # type: ignore[no-untyped-call]


def _to_data(value: Any, aliases: Optional[dict[int, str]] = None, found: Optional[dict[str, None]] = None) -> Any:
    """Convert SDK objects to JSON-compatible data.

    typesafe-sdk 0.6 models are msgspec Structs, which load_data_value does not recognize; later versions use pydantic.
    """
    aliases = {} if aliases is None else aliases
    found = {} if found is None else found
    if value is None or isinstance(value, (str, bytes)):
        return load_data_value(value)  # type: ignore[no-untyped-call]
    if isinstance(value, Mapping):
        return {str(k): _to_data(v, aliases, found) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_data(v, aliases, found) for v in value]
    reference = getattr(value, "_reference", None)
    if isinstance(reference, str):
        alias = aliases.get(id(value))
        if alias is None:
            found[reference] = None
        return alias or reference
    struct_fields = getattr(type(value), "__struct_fields__", None)
    if struct_fields is not None:
        data = {
            f: _to_data(getattr(value, f), aliases, found) for f in struct_fields if getattr(value, f, None) is not None
        }
        # A tagged Struct keeps its discriminator (e.g. "type") in its config, not in its fields
        struct_config = getattr(value, "__struct_config__", None)
        tag_field = getattr(struct_config, "tag_field", None)
        if tag_field:
            data[tag_field] = getattr(struct_config, "tag", None)
        return data
    model_fields = getattr(type(value), "model_fields", None)
    if isinstance(model_fields, dict):
        # Walk pydantic fields instead of using model_dump so images nested in questions keep their alias
        return {
            field.serialization_alias or field.alias or name: _to_data(getattr(value, name), aliases, found)
            for name, field in model_fields.items()
            if getattr(value, name, None) is not None
        }
    return load_data_value(value)  # type: ignore[no-untyped-call]
