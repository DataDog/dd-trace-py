"""LLMObs integration for vLLM V1 library."""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.contrib.internal.vllm.extractors import RequestData
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

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        """Set base tags on vLLM spans."""
        model_name = kwargs.get("model_name")
        if model_name:
            span.set_tag_str("vllm.request.model", model_name)
            span.set_tag_str("vllm.request.provider", "vllm")

    def _build_metadata(self, data: RequestData) -> Dict[str, Any]:
        """Extract metadata from request data."""
        md: Dict[str, Any] = {}

        for key in self._METADATA_FIELDS:
            val = getattr(data, key, None)
            if val is not None:
                md[key] = val

        return md

    def _build_metrics(self, data: RequestData, stats=None) -> Dict[str, Any]:
        """Build token and latency metrics from request data."""
        it = int(data.input_tokens or 0)
        ot = int(data.output_tokens or 0)
        metrics: Dict[str, Any] = {
            INPUT_TOKENS_METRIC_KEY: it,
            OUTPUT_TOKENS_METRIC_KEY: ot,
            TOTAL_TOKENS_METRIC_KEY: it + ot,
        }

        # Add latency metrics if stats are available
        if stats:
            if stats.first_token_latency:
                metrics[TIME_TO_FIRST_TOKEN_METRIC_KEY] = float(stats.first_token_latency)

            queued = stats.queued_ts
            scheduled = stats.scheduled_ts
            first_token = stats.first_token_ts
            last_token = stats.last_token_ts

            if queued and scheduled:
                metrics[TIME_IN_QUEUE_METRIC_KEY] = float(scheduled - queued)

            if scheduled and first_token:
                metrics[TIME_IN_MODEL_PREFILL_METRIC_KEY] = float(first_token - scheduled)

            if first_token and last_token and last_token > first_token:
                metrics[TIME_IN_MODEL_DECODE_METRIC_KEY] = float(last_token - first_token)

            if scheduled and last_token:
                metrics[TIME_IN_MODEL_INFERENCE_METRIC_KEY] = float(last_token - scheduled)

        return metrics

    def _build_embedding_context(self, data: RequestData, stats=None) -> Dict[str, Any]:
        """Build LLMObs context for embedding operations."""
        ctx: Dict[str, Any] = {
            SPAN_KIND: "embedding",
            METADATA: self._build_metadata(data),
            METRICS: self._build_metrics(data, stats),
        }

        docs: List[Document] = []
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

    def _build_completion_context(self, data: RequestData, stats=None) -> Dict[str, Any]:
        """Build LLMObs context for completion operations."""
        ctx: Dict[str, Any] = {
            SPAN_KIND: "llm",
            METADATA: self._build_metadata(data),
            METRICS: self._build_metrics(data, stats),
        }

        if data.prompt:
            ctx[INPUT_MESSAGES] = [{"content": data.prompt}]

        if data.output_text:
            ctx[OUTPUT_MESSAGES] = [{"content": data.output_text}]

        return ctx

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Set LLMObs tags on span."""
        data: Optional[RequestData] = kwargs.get("request_data")
        if data is None:
            return

        stats = kwargs.get("stats")
        ctx = (
            self._build_embedding_context(data, stats)
            if operation == "embedding"
            else self._build_completion_context(data, stats)
        )
        ctx[MODEL_NAME] = span.get_tag("vllm.request.model") or ""
        ctx[MODEL_PROVIDER] = span.get_tag("vllm.request.provider") or ""
        span._set_ctx_items(ctx)
