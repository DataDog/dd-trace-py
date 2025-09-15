from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import (
    INPUT_DOCUMENTS,
    INPUT_MESSAGES,
    INPUT_TOKENS_METRIC_KEY,
    METADATA,
    METRICS,
    MODEL_NAME,
    MODEL_PROVIDER,
    OUTPUT_MESSAGES,
    OUTPUT_TOKENS_METRIC_KEY,
    OUTPUT_VALUE,
    SPAN_KIND,
    TOTAL_TOKENS_METRIC_KEY,
)
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs.utils import Document
from ddtrace.trace import Span
from vllm import SamplingParams

log = get_logger(__name__)

MODEL = "vllm.request.model"

class VLLMIntegration(BaseLLMIntegration):
    _integration_name = "vllm"

    _SUPPLEMENTAL_TAG_MAP = {
        "finish_reason": "vllm.request.finish_reason",
        "stop_reason": "vllm.request.stop_reason",
        "lora_name": "vllm.request.lora_name",
        "request_id": "vllm.request.id",
    }

    # ----- base tags ---------------------------------------------------------
    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        model_name = kwargs.get("model_name")
        if model_name:
            span.set_tag_str(MODEL, model_name)

    # ----- helpers -----------------------------------------------------------
    def _build_metadata(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        md: Dict[str, Any] = {}
        sp: Optional[SamplingParams] = kwargs.get("sampling_params")
        if sp:
            for key in [
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "n",
                "presence_penalty",
                "frequency_penalty",
                "repetition_penalty",
                "seed",
            ]:
                val = getattr(sp, key, None)
                if val is not None:
                    md[key] = val

        for key in [
            "num_cached_tokens", "embedding_dim", "encoding_format",
        ] + list(self._SUPPLEMENTAL_TAG_MAP.keys()):
            val = kwargs.get(key)
            if val is not None:
                md[key] = val
        return md


    @staticmethod
    def _build_metrics(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        it = int(kwargs.get("input_tokens", 0) or 0)
        ot = int(kwargs.get("output_tokens", 0) or 0)
        return {
            INPUT_TOKENS_METRIC_KEY: it,
            OUTPUT_TOKENS_METRIC_KEY: ot,
            TOTAL_TOKENS_METRIC_KEY: it + ot,
        }

    # ----- embedding/completion contexts ------------------------------------
    def _build_embedding_context(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {
            SPAN_KIND: "embedding",
            MODEL_NAME: kwargs.get("model_name"),
            MODEL_PROVIDER: "vllm",
            METADATA: self._build_metadata(kwargs),
            METRICS: self._build_metrics(kwargs),
        }

        inputs = kwargs.get("input")
        prompt = kwargs.get("prompt")
        docs: List[Document] = []

        if inputs is not None:
            if isinstance(inputs, str) or (isinstance(inputs, list) and (not inputs or isinstance(inputs[0], int))):
                inputs = [inputs]
            if isinstance(inputs, list):
                docs = [Document(text=str(d)) for d in inputs]
        elif prompt:
            docs = [Document(text=str(prompt))]

        if docs:
            ctx[INPUT_DOCUMENTS] = docs

        num_embeddings = kwargs.get("num_embeddings")
        if not isinstance(num_embeddings, int) or num_embeddings <= 0:
            num_embeddings = len(docs) if docs else 1
        dim = kwargs.get("embedding_dim")
        ctx[OUTPUT_VALUE] = f"[{num_embeddings} embedding(s) returned with size {dim}]" if dim else f"[{num_embeddings} embedding(s) returned]"
        return ctx

    def _build_completion_context(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {
            SPAN_KIND: "llm",
            MODEL_NAME: kwargs.get("model_name"),
            MODEL_PROVIDER: "vllm",
            METADATA: self._build_metadata(kwargs),
            METRICS: self._build_metrics(kwargs),
        }

        prompt = kwargs.get("prompt")
        if prompt:
            ctx[INPUT_MESSAGES] = [{"content": prompt}]

        output_text = kwargs.get("output_text")
        if output_text:
            ctx[OUTPUT_MESSAGES] = [{"content": output_text}]

        log.debug(
            "[VLLM DD] build_completion_context: model=%s has_input=%s has_output=%s",
            kwargs.get("model_name"),
            bool(prompt),
            bool(output_text),
        )
        return ctx


    # ddtrace integration entry (name required by BaseLLMIntegration)
    def _llmobs_set_tags(self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any] = None, operation: str = "") -> None:
        ctx = self._build_embedding_context(kwargs) if operation == "embedding" else self._build_completion_context(kwargs)
        span._set_ctx_items(ctx)
        
        for key, tag in self._SUPPLEMENTAL_TAG_MAP.items():
            val = kwargs.get(key)
            if val is not None:
                span.set_tag_str(tag, str(val))

        hdr_model = getattr(span, "_get_ctx_item", lambda *_: None)("x-datadog-vllm-model")
        if hdr_model and not kwargs.get("model_name"):
            self._set_base_span_tags(span, model_name=hdr_model)
