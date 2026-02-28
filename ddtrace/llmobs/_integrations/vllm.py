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
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_MODEL_DECODE_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_MODEL_INFERENCE_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_MODEL_PREFILL_METRIC_KEY
from ddtrace.llmobs._constants import TIME_IN_QUEUE_METRIC_KEY
from ddtrace.llmobs._constants import TIME_TO_FIRST_TOKEN_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
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

    def _set_embedding_tags(
        self, span: Span, data: RequestData, latency_metrics: Optional[LatencyMetrics] = None
    ) -> None:
        """Set LLMObs tags for embedding operations."""
        docs: list[Document] = []
        if data.prompt:
            docs = [Document(text=data.prompt)]
        elif data.input_:
            docs = [Document(text=str(data.input_))]

        num_emb = data.num_embeddings
        dim = data.embedding_dim
        output_value = (
            f"[{num_emb} embedding(s) returned with size {dim}]" if dim else f"[{num_emb} embedding(s) returned]"
        )

        _annotate_llmobs_span_data(
            span,
            kind="embedding",
            model_name=span.get_tag(TAG_MODEL) or "",
            model_provider=span.get_tag(TAG_PROVIDER) or "",
            metadata=self._build_metadata(data),
            metrics=self._build_metrics(data, latency_metrics),
            input_documents=docs if docs else None,
            output_value=output_value,
        )

    def _set_completion_tags(
        self, span: Span, data: RequestData, latency_metrics: Optional[LatencyMetrics] = None
    ) -> None:
        """Set LLMObs tags for completion operations."""
        input_messages = parse_prompt_to_messages(data.prompt) if data.prompt else None
        output_messages = [Message(role="assistant", content=data.output_text)] if data.output_text else None

        _annotate_llmobs_span_data(
            span,
            kind="llm",
            model_name=span.get_tag(TAG_MODEL) or "",
            model_provider=span.get_tag(TAG_PROVIDER) or "",
            metadata=self._build_metadata(data),
            metrics=self._build_metrics(data, latency_metrics),
            input_messages=input_messages,
            output_messages=output_messages,
        )

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
        if operation == "embedding":
            self._set_embedding_tags(span, data, latency_metrics)
        else:
            self._set_completion_tags(span, data, latency_metrics)
