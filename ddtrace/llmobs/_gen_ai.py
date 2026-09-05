"""Emission of ``gen_ai.*`` attributes onto the APM span.

Kept separate from ``_llmobs`` and ``_integrations`` so both the LLMObs-enabled path
(``LLMObs._prepare_llmobs_span_data``, at span finish) and the LLMObs-disabled path
(``BaseLLMIntegration._apply_shadow_metrics``, during the span) can write the same keys.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_APPLICATION_NAME_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_CONVERSATION_ID_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_OPERATION_NAME_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_PROVIDER_NAME_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_REQUEST_MODEL_TAG_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import UNKNOWN_MODEL_NAME
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER


if TYPE_CHECKING:
    from ddtrace.llmobs._writer import LLMObsSpanData


# Token usage is only meaningful for the kinds that actually call a model; other kinds can carry
# unrelated metrics that would be misleading under a gen_ai.usage.* key.
_TOKEN_METRIC_SPAN_KINDS = ("llm", "embedding")

_TOKEN_METRIC_KEYS = (
    (INPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_INPUT_TOKENS_METRIC_KEY),
    (OUTPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_OUTPUT_TOKENS_METRIC_KEY),
    (TOTAL_TOKENS_METRIC_KEY, GEN_AI_USAGE_TOTAL_TOKENS_METRIC_KEY),
    (CACHE_READ_INPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS_METRIC_KEY),
    (CACHE_WRITE_INPUT_TOKENS_METRIC_KEY, GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS_METRIC_KEY),
)


def gen_ai_apm_tags_enabled() -> bool:
    return bool(config._llmobs_gen_ai_apm_tags_enabled)


def set_gen_ai_apm_tags(
    span: Span,
    span_kind: Optional[str],
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    metrics: Optional[dict[str, Any]] = None,
    ml_app: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Write the scalar gen_ai.* attributes onto the APM span.

    Model name and provider are normalized here rather than by the caller, so that a span tagged
    from the LLMObs-disabled path and the same span tagged at finish from the normalized
    meta_struct produce the same facet value instead of splitting it.

    Values that are simply absent are skipped rather than written as a placeholder, so a missing
    tag means the tracer had nothing to report instead of reporting something wrong.
    """
    if not gen_ai_apm_tags_enabled():
        return
    if span_kind:
        span.set_tag(GEN_AI_OPERATION_NAME_TAG_KEY, span_kind)
    if span_kind in _TOKEN_METRIC_SPAN_KINDS:
        # Mirrors _normalize_llmobs_meta: a model-backed span always reports a model and provider,
        # falling back to "unknown" so the facet has no gaps.
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
    if span_kind in _TOKEN_METRIC_SPAN_KINDS and metrics:
        for llmobs_key, gen_ai_key in _TOKEN_METRIC_KEYS:
            value = metrics.get(llmobs_key)
            if value is not None:
                span._set_attribute(gen_ai_key, value)


def set_gen_ai_apm_tags_from_llmobs_data(span: Span, llmobs_data: "LLMObsSpanData") -> None:
    """Write gen_ai.* attributes from a span's finalized LLMObs meta_struct.

    Must run after `_normalize_llmobs_meta`, which is what defaults the model name, lowercases the
    provider, and settles the span kind. Reading the same structure the LLMObs event is assembled
    from is what keeps the APM tag and the LLMObs event in agreement for a given span.
    """
    if not gen_ai_apm_tags_enabled():
        return
    llmobs_meta = llmobs_data.get(LLMOBS_STRUCT.META) or {}
    set_gen_ai_apm_tags(
        span,
        span_kind=llmobs_meta.get(LLMOBS_STRUCT.SPAN, {}).get(LLMOBS_STRUCT.KIND),
        model_name=llmobs_meta.get(LLMOBS_STRUCT.MODEL_NAME),
        model_provider=llmobs_meta.get(LLMOBS_STRUCT.MODEL_PROVIDER),
        metrics=llmobs_data.get(LLMOBS_STRUCT.METRICS),
        ml_app=llmobs_data.get(LLMOBS_STRUCT.ML_APP),
        session_id=llmobs_data.get(LLMOBS_STRUCT.SESSION_ID),
    )
