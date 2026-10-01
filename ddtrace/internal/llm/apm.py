"""APM span tagging for LLM requests that must work whether or not LLM Observability is loaded."""

from collections.abc import Mapping
from typing import Any
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace import config
from ddtrace.internal.llm.constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import GEN_AI_APPLICATION_NAME_TAG_KEY
from ddtrace.internal.llm.constants import GEN_AI_CONVERSATION_ID_TAG_KEY
from ddtrace.internal.llm.constants import GEN_AI_OPERATION_NAME_TAG_KEY
from ddtrace.internal.llm.constants import GEN_AI_PROVIDER_NAME_TAG_KEY
from ddtrace.internal.llm.constants import GEN_AI_REQUEST_MODEL_TAG_KEY
from ddtrace.internal.llm.constants import GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_MODEL_NAME_TAG_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_MODEL_PROVIDER_TAG_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY
from ddtrace.internal.llm.constants import LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import LLMOBS_ARTIFICIAL_GEN_AI_TAGS_KEY
from ddtrace.internal.llm.constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import REASONING_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.internal.llm.constants import UNKNOWN_MODEL_NAME
from ddtrace.internal.llm.constants import UNKNOWN_MODEL_PROVIDER


class TaggableSpan(Protocol):
    # Structural stand-in for ddtrace.trace.Span: ddtrace.internal must not import the tracing product.
    def set_tag(self, key: str, value: Optional[str] = None) -> None: ...
    def _set_attribute(self, key: str, value: Union[str, int, float]) -> None: ...


TOKEN_METRIC_SPAN_KINDS = ("llm", "embedding")

_GEN_AI_TOKEN_METRIC_KEYS = (
    (INPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY),
    (OUTPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY),
    (TOTAL_TOKENS_METRIC_KEY, GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY),
    (CACHE_READ_INPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY),
    (CACHE_WRITE_INPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY),
    (REASONING_OUTPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_REASONING_OUTPUT_TOKENS_METRIC_KEY),
)

_SHADOW_TOKEN_METRIC_KEYS = (
    (INPUT_TOKENS_METRIC_KEY, LLMOBS_APM_SHADOW_INPUT_TOKENS_METRIC_KEY),
    (OUTPUT_TOKENS_METRIC_KEY, LLMOBS_APM_SHADOW_OUTPUT_TOKENS_METRIC_KEY),
    (TOTAL_TOKENS_METRIC_KEY, LLMOBS_APM_SHADOW_TOTAL_TOKENS_METRIC_KEY),
    (CACHE_READ_INPUT_TOKENS_METRIC_KEY, LLMOBS_APM_SHADOW_CACHE_READ_INPUT_TOKENS_METRIC_KEY),
    (CACHE_WRITE_INPUT_TOKENS_METRIC_KEY, LLMOBS_APM_SHADOW_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY),
)


def is_instrumented_proxy_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    instrumented_proxy_urls = config._llmobs_instrumented_proxy_urls or set()
    return base_url in instrumented_proxy_urls


def set_gen_ai_apm_tags(
    span: TaggableSpan,
    span_kind: Optional[str],
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    metrics: Optional[Mapping[str, Any]] = None,
    ml_app: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Write the scalar gen_ai.* attributes onto the APM span."""
    if span_kind:
        span.set_tag(GEN_AI_OPERATION_NAME_TAG_KEY, span_kind)
    if span_kind in TOKEN_METRIC_SPAN_KINDS:
        # Mirrors _normalize_llmobs_meta: model-backed spans always report a model and provider.
        span.set_tag(GEN_AI_REQUEST_MODEL_TAG_KEY, model_name or UNKNOWN_MODEL_NAME)
        span.set_tag(GEN_AI_PROVIDER_NAME_TAG_KEY, (model_provider or UNKNOWN_MODEL_PROVIDER).lower())
    else:
        if model_name:
            span.set_tag(GEN_AI_REQUEST_MODEL_TAG_KEY, model_name)
        if model_provider:
            span.set_tag(GEN_AI_PROVIDER_NAME_TAG_KEY, model_provider.lower())
    if ml_app:
        span.set_tag(GEN_AI_APPLICATION_NAME_TAG_KEY, ml_app)
    if session_id:
        span.set_tag(GEN_AI_CONVERSATION_ID_TAG_KEY, session_id)
    if span_kind in TOKEN_METRIC_SPAN_KINDS and metrics:
        for llmobs_key, gen_ai_key in _GEN_AI_TOKEN_METRIC_KEYS:
            value = metrics.get(llmobs_key)
            if value is not None:
                span._set_attribute(gen_ai_key, value)
    # Without this tag, the backend processor identifies gen_ai tags on the APM span and creates
    # a duplicate LLMObs span.
    span.set_tag(LLMOBS_ARTIFICIAL_GEN_AI_TAGS_KEY, "true")


def apply_shadow_metrics(
    span: TaggableSpan,
    metrics: Optional[Mapping[str, Any]],
    span_kind: str,
    llmobs_enabled: bool,
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
) -> None:
    """Set shadow metric/tag values on the APM span from extracted metrics."""
    span.set_tag(LLMOBS_APM_SHADOW_SPAN_KIND_TAG_KEY, span_kind)
    span._set_attribute(LLMOBS_APM_SHADOW_ENABLED_METRIC_KEY, 1 if llmobs_enabled else 0)
    if model_name:
        span.set_tag(LLMOBS_APM_SHADOW_MODEL_NAME_TAG_KEY, model_name)
    if model_provider:
        span.set_tag(LLMOBS_APM_SHADOW_MODEL_PROVIDER_TAG_KEY, model_provider)
    # Only when LLMObs is off; otherwise _prepare_llmobs_span_data emits these at span finish
    # with better values. set_gen_ai_apm_tags also marks the span as artificially tagged, so
    # the backend can tell these apart from user-set gen_ai.* tags and skip creating a
    # duplicate LLMObs span for it.
    if not llmobs_enabled:
        set_gen_ai_apm_tags(span, span_kind, model_name=model_name, model_provider=model_provider, metrics=metrics)
    if span_kind in TOKEN_METRIC_SPAN_KINDS and metrics:
        for llmobs_key, shadow_key in _SHADOW_TOKEN_METRIC_KEYS:
            value = metrics.get(llmobs_key)
            if value is not None:
                span._set_attribute(shadow_key, value)
