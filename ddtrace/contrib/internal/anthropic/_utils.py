"""APM span tagging for Anthropic requests.

This is owned by the contrib integration so APM spans carry the same model, provider, and token
usage tags whether or not LLM Observability is loaded. AnthropicIntegration delegates here too.
"""

from typing import Any
from typing import Optional

from ddtrace.internal.llm.apm import apply_shadow_metrics
from ddtrace.internal.llm.constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import PROXY_REQUEST
from ddtrace.internal.llm.constants import REQUEST_BASE_URL
from ddtrace.internal.llm.constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.trace import Span


MODEL = "anthropic.request.model"

_ANTHROPIC_MODEL_PROVIDER = "anthropic"
_BEDROCK_MODEL_PROVIDER = "amazon"
_VERTEX_MODEL_PROVIDER = "google"


def get_base_url(instance: Any) -> Optional[str]:
    client = getattr(instance, "_client", None)
    base_url = getattr(client, "_base_url", None) if client else None
    return str(base_url) if base_url else None


def set_base_span_tags(span: Span, model: Optional[str] = None, instance: Any = None) -> None:
    """Set base level tags that should be present on all Anthropic spans (if they are not None)."""
    # Store base_url per-span rather than on a shared object so a streaming span that finalizes
    # after concurrent requests still resolves the right provider.
    base_url = get_base_url(instance)
    if base_url is not None:
        span._set_ctx_item(REQUEST_BASE_URL, base_url)
    if model is not None:
        span._set_attribute(MODEL, model)


def get_model_provider(span: Span) -> str:
    """Resolve the model provider from the request base_url captured on the span.

    Returns "amazon" if the base_url contains "bedrock".
    Returns "google" if the base_url contains "google".
    Returns "anthropic" if the base_url contains "anthropic".
    Returns "unknown" when the base_url is missing or unrecognized.
    """
    base_url = (span._get_ctx_item(REQUEST_BASE_URL) or "").lower()
    if not base_url:
        return UNKNOWN_MODEL_PROVIDER
    if "bedrock" in base_url:
        return _BEDROCK_MODEL_PROVIDER
    if "google" in base_url:
        return _VERTEX_MODEL_PROVIDER
    if "anthropic" in base_url:
        return _ANTHROPIC_MODEL_PROVIDER
    return UNKNOWN_MODEL_PROVIDER


def extract_usage(usage: Any) -> Optional[dict[str, int]]:
    if not usage:
        return None
    input_tokens = _get_attr(usage, "input_tokens", None)
    output_tokens = _get_attr(usage, "output_tokens", None)
    cache_write_tokens = _get_attr(usage, "cache_creation_input_tokens", None)
    cache_read_tokens = _get_attr(usage, "cache_read_input_tokens", None)

    metrics = {}

    # `input_tokens` in the returned usage is the number of non-cached tokens. We normalize it to mean
    # the total tokens sent to the model to be consistent with other model providers.
    metrics[INPUT_TOKENS_METRIC_KEY] = (input_tokens or 0) + (cache_write_tokens or 0) + (cache_read_tokens or 0)

    if output_tokens is not None:
        metrics[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if INPUT_TOKENS_METRIC_KEY in metrics and output_tokens is not None:
        metrics[TOTAL_TOKENS_METRIC_KEY] = metrics[INPUT_TOKENS_METRIC_KEY] + output_tokens

    if cache_write_tokens is not None:
        metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_write_tokens
        cache_creation_breakdown = _get_attr(usage, "cache_creation", {})
        cache_creation_1h_tokens = _get_attr(cache_creation_breakdown, "ephemeral_1h_input_tokens", None)
        cache_creation_5m_tokens = _get_attr(cache_creation_breakdown, "ephemeral_5m_input_tokens", None)
        if cache_creation_1h_tokens is None and cache_creation_5m_tokens is None:
            # Legacy API response without cache_creation breakdown; assume all writes are 5m TTL.
            cache_creation_5m_tokens = cache_write_tokens
        metrics[CACHE_WRITE_1H_INPUT_TOKENS_METRIC_KEY] = cache_creation_1h_tokens or 0
        metrics[CACHE_WRITE_5M_INPUT_TOKENS_METRIC_KEY] = cache_creation_5m_tokens or 0

    if cache_read_tokens is not None:
        metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cache_read_tokens
    return metrics


def get_span_kind(span: Span) -> str:
    return "workflow" if span._get_ctx_item(PROXY_REQUEST) else "llm"


def set_apm_shadow_tags(span: Span, response: Any, llmobs_enabled: bool) -> None:
    span_kind = get_span_kind(span)
    usage = _get_attr(response, "usage", {})
    metrics = extract_usage(usage) if span_kind != "workflow" else {}
    apply_shadow_metrics(
        span,
        metrics,
        span_kind,
        llmobs_enabled,
        model_name=span.get_tag(MODEL),
        model_provider=get_model_provider(span),
    )


class AnthropicApmTagger:
    """Implements LlmApmTagger for LlmRequestEvent.apm_tagger."""

    def get_base_url(self, instance: Any) -> Optional[str]:
        return get_base_url(instance)

    def set_base_span_tags(self, span: Span, model: Optional[str], instance: Any) -> None:
        set_base_span_tags(span, model=model, instance=instance)

    def set_apm_shadow_tags(self, span: Span, response: Any, llmobs_enabled: bool) -> None:
        set_apm_shadow_tags(span, response, llmobs_enabled)


apm_tagger = AnthropicApmTagger()
