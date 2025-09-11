from typing import Any, Dict, List, Optional

from ddtrace.llmobs._constants import (
    INPUT_MESSAGES, INPUT_TOKENS_METRIC_KEY, METADATA, METRICS, MODEL_NAME,
    MODEL_PROVIDER, OUTPUT_MESSAGES, OUTPUT_TOKENS_METRIC_KEY, SPAN_KIND,
    TOTAL_TOKENS_METRIC_KEY, INPUT_DOCUMENTS, OUTPUT_VALUE,
)
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span
from ddtrace.llmobs.utils import Document
from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class VLLMIntegration(BaseLLMIntegration):
    """Clean, refactored vLLM integration for LLMObs."""
    
    _integration_name = "vllm"

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        """Set base span tags."""
        model_name = kwargs.get("model_name")
        if model_name:
            span.set_tag_str("vllm.request.model", model_name)
            # Also set generic model tag used in LLMObs UI
            span.set_tag_str("model.name", model_name)

    def _extract_model_info(self, model_name: str) -> tuple[str, str]:
        """Extract provider and short model name."""
        if not model_name or "/" not in model_name:
            return "vllm", model_name or ""
        return model_name.split("/", 1)

    def _build_metadata(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build metadata dictionary from sampling params and request info."""
        metadata = {}
        
        # Sampling parameters
        sampling_params = kwargs.get("sampling_params")
        if sampling_params:
            for key in ["temperature", "max_tokens", "top_p", "top_k", "n", 
                       "presence_penalty", "frequency_penalty", "repetition_penalty", "seed"]:
                value = getattr(sampling_params, key, None)
                if value is not None:
                    metadata[key] = value
        
        # Request metadata
        for key in ["finish_reason", "stop_reason", "num_cached_tokens", 
                   "lora_name", "request_id", "embedding_dim", "encoding_format"]:
            value = kwargs.get(key)
            if value is not None:
                metadata[key] = value
        
        return metadata

    def _build_metrics(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build token metrics dictionary."""
        input_tokens = int(kwargs.get("input_tokens", 0) or 0)
        output_tokens = int(kwargs.get("output_tokens", 0) or 0)
        
        return {
            INPUT_TOKENS_METRIC_KEY: input_tokens,
            OUTPUT_TOKENS_METRIC_KEY: output_tokens,
            TOTAL_TOKENS_METRIC_KEY: input_tokens + output_tokens,
        }

    def _build_embedding_context(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build context for embedding operations."""
        provider, short_model = self._extract_model_info(kwargs.get("model_name", ""))
        
        context = {
            SPAN_KIND: "embedding",
            MODEL_NAME: short_model,
            MODEL_PROVIDER: provider,
            METADATA: self._build_metadata(kwargs),
            METRICS: self._build_metrics(kwargs),
        }
        
        # Handle input documents
        embedding_inputs = kwargs.get("input")
        prompt = kwargs.get("prompt")
        
        docs = []
        if embedding_inputs is not None:
            # Normalize: string or list[int] treated as a single document
            if isinstance(embedding_inputs, str) or (
                isinstance(embedding_inputs, list) and (len(embedding_inputs) == 0 or isinstance(embedding_inputs[0], int))
            ):
                embedding_inputs = [embedding_inputs]
            if isinstance(embedding_inputs, list):
                docs = [Document(text=str(doc)) for doc in embedding_inputs]
        elif prompt:
            docs = [Document(text=str(prompt))]
        
        if docs:
            context[INPUT_DOCUMENTS] = docs
        
        # Build output value description
        num_embeddings = kwargs.get("num_embeddings")
        if not isinstance(num_embeddings, int) or num_embeddings <= 0:
            num_embeddings = len(docs) if docs else 1
        embedding_dim = kwargs.get("embedding_dim")
        
        if embedding_dim:
            context[OUTPUT_VALUE] = f"[{num_embeddings} embedding(s) returned with size {embedding_dim}]"
        else:
            context[OUTPUT_VALUE] = f"[{num_embeddings} embedding(s) returned]"
        
        return context

    def _build_completion_context(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build context for completion operations."""
        provider, short_model = self._extract_model_info(kwargs.get("model_name", ""))
        
        context = {
            SPAN_KIND: "llm",
            MODEL_NAME: short_model,
            MODEL_PROVIDER: provider,
            METADATA: self._build_metadata(kwargs),
            METRICS: self._build_metrics(kwargs),
        }
        
        # Input/output messages
        prompt = kwargs.get("prompt")
        if prompt:
            context[INPUT_MESSAGES] = [{"content": prompt}]
        
        output_text = kwargs.get("output_text")
        if output_text:
            context[OUTPUT_MESSAGES] = [{"content": output_text}]
        log.debug("[VLLM DD] build_completion_context: model=%s has_input=%s has_output=%s", short_model, bool(prompt), bool(output_text))
        
        return context

    def _set_supplemental_tags(self, span: Span, kwargs: Dict[str, Any]) -> None:
        """Set supplemental span tags for querying."""
        tag_mappings = {
            "finish_reason": "vllm.request.finish_reason",
            "stop_reason": "vllm.request.stop_reason", 
            "lora_name": "vllm.request.lora_name",
            "request_id": "vllm.request.id",
        }
        
        for key, tag_name in tag_mappings.items():
            value = kwargs.get(key)
            if value is not None:
                span.set_tag_str(tag_name, str(value))
        # Adopt model name from propagated trace headers if present
        hdr_model = getattr(span, "_get_ctx_item", lambda *_: None)("x-datadog-vllm-model") if hasattr(span, "_get_ctx_item") else None
        if hdr_model and not kwargs.get("model_name"):
            span.set_tag_str("vllm.request.model", str(hdr_model))

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = ""
    ) -> None:
        """Set LLMObs tags on span."""
        # Determine operation type only based on explicit operation arg
        is_embedding = (operation == "embedding")
        
        # Build appropriate context
        if is_embedding:
            context = self._build_embedding_context(kwargs)
        else:
            context = self._build_completion_context(kwargs)
        
        # Set context items and supplemental tags
        span._set_ctx_items(context)
        self._set_supplemental_tags(span, kwargs)
 
