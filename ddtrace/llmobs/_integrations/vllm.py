"""LLMObs integration for vLLM V1 library."""

from __future__ import annotations

from typing import Any
from typing import Optional

from ddtrace.contrib.internal.vllm._constants import PROVIDER_NAME
from ddtrace.contrib.internal.vllm._constants import TAG_MODEL
from ddtrace.contrib.internal.vllm._constants import TAG_PROVIDER
from ddtrace.contrib.internal.vllm.extractors import LatencyMetrics
from ddtrace.contrib.internal.vllm.extractors import RequestData
from ddtrace.contrib.internal.vllm.extractors import parse_prompt_to_messages
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TIME_IN_MODEL_DECODE_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_MODEL_INFERENCE_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_MODEL_PREFILL_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_QUEUE_METRIC_KEY
from ddtrace.llmobs._constants import TIME_TO_FIRST_TOKEN_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.utils import Document
from ddtrace.trace import Span


class VLLMIntegration(BaseLLMIntegration):
    """LLMObs integration for vLLM V1 library."""

    _integration_name = "vllm"

    _METADATA_FIELDS = {
        "temperature",
        "max_tokens",
        "top_p",
        "n",
        "num_cached_tokens",
        "embedding_dim",
        "finish_reason",
        "lora_name",
    }

    # Mapping from LatencyMetrics attributes to LLMObs metric keys
    _LLMOBS_LATENCY_METRIC_MAP = {
        "time_to_first_token": TIME_TO_FIRST_TOKEN_METRIC_KEY,
        "time_in_queue": TIME_IN_QUEUE_METRIC_KEY,
        "time_in_model_prefill": TIME_IN_MODEL_PREFILL_METRIC_KEY,
        "time_in_model_decode": TIME_IN_MODEL_DECODE_METRIC_KEY,
        "time_in_model_inference": TIME_IN_MODEL_INFERENCE_METRIC_KEY,
    }

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        """Set base tags on vLLM spans."""
        model_name = kwargs.get("model_name")
        if model_name:
            span._set_tag_str(TAG_MODEL, model_name)
            span._set_tag_str(TAG_PROVIDER, PROVIDER_NAME)

    def _build_metadata(self, data: RequestData) -> dict[str, Any]:
        """Extract metadata from request data."""
        md: dict[str, Any] = {}

        for key in self._METADATA_FIELDS:
            val = getattr(data, key, None)
            if val is not None:
                md[key] = val

        return md

    def _build_metrics(self, data: RequestData, latency_metrics: Optional[LatencyMetrics] = None) -> dict[str, Any]:
        """Build token and latency metrics from request data."""
        it = int(data.input_tokens or 0)
        ot = int(data.output_tokens or 0)
        metrics: dict[str, Any] = {
            INPUT_TOKENS_METRIC_KEY: it,
            OUTPUT_TOKENS_METRIC_KEY: ot,
            TOTAL_TOKENS_METRIC_KEY: it + ot,
        }

        if latency_metrics:
            for attr, constant_key in self._LLMOBS_LATENCY_METRIC_MAP.items():
                value = getattr(latency_metrics, attr, None)
                if value is not None:
                    metrics[constant_key] = value

        return metrics

    def _build_embedding_context(
        self, data: RequestData, latency_metrics: Optional[LatencyMetrics] = None
    ) -> dict[str, Any]:
        """Build LLMObs context for embedding operations."""
        ctx: dict[str, Any] = {
            SPAN_KIND: "embedding",
            METADATA: self._build_metadata(data),
            METRICS: self._build_metrics(data, latency_metrics),
        }

        docs: list[Document] = []
        if data.prompt:
            docs = [Document(text=data.prompt)]
        elif data.input_:
            docs = [Document(text=str(data.input_))]

        if docs:
            ctx[INPUT_DOCUMENTS] = docs

        num_emb = data.num_embeddings
        dim = data.embedding_dim
        ctx[OUTPUT_VALUE] = (
            f"[{num_emb} embedding(s) returned with size {dim}]" if dim else f"[{num_emb} embedding(s) returned]"
        )

        return ctx

    def _build_completion_context(
        self, data: RequestData, latency_metrics: Optional[LatencyMetrics] = None
    ) -> dict[str, Any]:
        """Build LLMObs context for completion operations."""
        ctx: dict[str, Any] = {
            SPAN_KIND: "llm",
            METADATA: self._build_metadata(data),
            METRICS: self._build_metrics(data, latency_metrics),
        }

        if data.prompt:
            ctx[INPUT_MESSAGES] = parse_prompt_to_messages(data.prompt)

        if data.output_text:
            ctx[OUTPUT_MESSAGES] = [Message(role="assistant", content=data.output_text)]

        return ctx

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Set LLMObs tags on span."""
        data: Optional[RequestData] = kwargs.get("request_data")
        if data is None:
            return

        latency_metrics = kwargs.get("latency_metrics")
        ctx = (
            self._build_embedding_context(data, latency_metrics)
            if operation == "embedding"
            else self._build_completion_context(data, latency_metrics)
        )
        ctx[MODEL_NAME] = span.get_tag(TAG_MODEL) or ""
        ctx[MODEL_PROVIDER] = span.get_tag(TAG_PROVIDER) or ""
        span._set_ctx_items(ctx)
