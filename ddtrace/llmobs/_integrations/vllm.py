from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.contrib.internal.vllm.data_extractors import RequestData
from ddtrace.internal.logger import get_logger
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
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs.utils import Document
from ddtrace.trace import Span


log = get_logger(__name__)

VLLM_MODEL_NAME = "vllm.request.model"
VLLM_MODEL_PROVIDER = "vllm.request.provider"


class VLLMIntegration(BaseLLMIntegration):
    """LLMObs integration for vLLM library.

    Handles both V0 (engine-side) and V1 (client-side) tracing modes,
    supporting completion, embedding, and cross-encoding operations.
    """

    _integration_name = "vllm"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cache for captured prompts keyed by request_id
        self._dd_captured_prompts: Dict[str, str] = {}

    _METADATA_FIELDS = {
        # SamplingParams
        "temperature",
        "max_tokens",
        "top_p",
        "top_k",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "repetition_penalty",
        "seed",
        # Misc.
        "num_cached_tokens",
        "embedding_dim",
        "encoding_format",
        "finish_reason",
        "stop_reason",
        "lora_name",
    }

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        """Set base tags (model name, provider) on vLLM spans."""
        model_name = kwargs.get("model_name")
        if model_name:
            span.set_tag_str(VLLM_MODEL_NAME, model_name)
            span.set_tag_str(VLLM_MODEL_PROVIDER, "vllm")

    def capture_prompt(self, request_id: str, prompt: str) -> None:
        """Store a captured prompt for later retrieval (used in V0 pooling requests)."""
        if request_id and prompt:
            self._dd_captured_prompts[str(request_id)] = prompt

    def get_captured_prompt(self, request_id: str) -> Optional[str]:
        """Retrieve a previously captured prompt (used in V0 pooling requests)."""
        return self._dd_captured_prompts.get(str(request_id)) if request_id else None

    def _build_metadata(self, data: RequestData) -> Dict[str, Any]:
        md: Dict[str, Any] = {}
        if data.sampling_params:
            for key in self._METADATA_FIELDS:
                val = getattr(data.sampling_params, key, None)
                if val is not None:
                    md[key] = val

        for key in self._METADATA_FIELDS:
            if hasattr(data, key):
                val = getattr(data, key, None)
                if val is not None:
                    md[key] = val
        return md

    @staticmethod
    def _build_metrics(data: RequestData) -> Dict[str, Any]:
        it = int(data.input_tokens or 0)
        ot = int(data.output_tokens or 0)
        return {
            INPUT_TOKENS_METRIC_KEY: it,
            OUTPUT_TOKENS_METRIC_KEY: ot,
            TOTAL_TOKENS_METRIC_KEY: it + ot,
        }

    def _build_embedding_context(self, data: RequestData) -> Dict[str, Any]:
        """Build LLMObs context for embedding operations."""
        ctx: Dict[str, Any] = {
            SPAN_KIND: "embedding",
            METADATA: self._build_metadata(data),
            METRICS: self._build_metrics(data),
        }

        docs: List[Document] = []
        inputs = data.input_
        if inputs is not None:
            if isinstance(inputs, str) or (isinstance(inputs, list) and (not inputs or isinstance(inputs[0], int))):
                inputs = [inputs]
            if isinstance(inputs, list):
                docs = [Document(text=str(d)) for d in inputs]
        elif data.prompt:
            docs = [Document(text=str(data.prompt))]

        if docs:
            ctx[INPUT_DOCUMENTS] = docs

        num_embeddings = data.num_embeddings
        if not isinstance(num_embeddings, int) or num_embeddings <= 0:
            num_embeddings = len(docs) if docs else 1
        dim = data.embedding_dim
        ctx[OUTPUT_VALUE] = (
            f"[{num_embeddings} embedding(s) returned with size {dim}]"
            if dim
            else f"[{num_embeddings} embedding(s) returned]"
        )
        return ctx

    def _build_completion_context(self, data: RequestData) -> Dict[str, Any]:
        """Build LLMObs context for completion operations."""
        ctx: Dict[str, Any] = {
            SPAN_KIND: "llm",
            METADATA: self._build_metadata(data),
            METRICS: self._build_metrics(data),
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
        data: Optional[RequestData] = kwargs.get("request_data")
        if data is None:
            log.debug("VLLMIntegration._llmobs_set_tags called without RequestData.")
            return

        ctx = self._build_embedding_context(data) if operation == "embedding" else self._build_completion_context(data)
        ctx[MODEL_NAME] = span.get_tag(VLLM_MODEL_NAME) or ""
        ctx[MODEL_PROVIDER] = span.get_tag(VLLM_MODEL_PROVIDER) or ""
        span._set_ctx_items(ctx)
