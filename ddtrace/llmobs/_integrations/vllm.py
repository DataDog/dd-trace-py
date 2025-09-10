from typing import Any, Dict, List, Optional

from ddtrace.llmobs._constants import (
    INPUT_MESSAGES,
    INPUT_TOKENS_METRIC_KEY,
    METADATA,
    METRICS,
    MODEL_NAME,
    MODEL_PROVIDER,
    OUTPUT_MESSAGES,
    OUTPUT_TOKENS_METRIC_KEY,
    SPAN_KIND,
    TOTAL_TOKENS_METRIC_KEY,
    INPUT_DOCUMENTS,
    OUTPUT_VALUE,
)
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.utils import Document

log = get_logger(__name__)


class VLLMIntegration(BaseLLMIntegration):
    _integration_name = "vllm"

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        """Set base level tags that should be present on all vLLM spans."""
        # Set the model name if available from the engine config
        model_name = kwargs.get("model_name")
        if model_name:
            span.set_tag_str("vllm.request.model", model_name)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = ""
    ) -> None:
        """Set LLMObs tags on the span (V1 path).

        Caller passes data via kwargs: prompt, output_text, input_tokens, output_tokens,
        sampling_params, model_name. This mirrors the OpenAI integration style and keeps
        all logic centralized in the integration rather than the patch wrapper.
        """
        log.debug(
            "[VLLM INTEGRATION DEBUG] _llmobs_set_tags called; operation=%s response=%s kwargs_keys=%s",
            operation,
            type(response).__name__ if response is not None else None,
            list(kwargs.keys()),
        )
        model_name = kwargs.get("model_name") or span.get_tag("vllm.request.model") or ""
        # Sampling metadata
        metadata_ctx: Dict[str, Any] = {}
        sp = kwargs.get("sampling_params")
        if sp is not None:
            for key in (
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "n",
                "presence_penalty",
                "frequency_penalty",
                "repetition_penalty",
                "seed",
            ):
                val = getattr(sp, key, None)
                if val is not None:
                    metadata_ctx[key] = val

        # Additional metadata from kwargs
        # finish/stop reasons, request identifiers, lora, operation, cached tokens
        if kwargs.get("finish_reason") is not None:
            metadata_ctx["finish_reason"] = kwargs.get("finish_reason")
        if kwargs.get("stop_reason") is not None:
            metadata_ctx["stop_reason"] = kwargs.get("stop_reason")
        if kwargs.get("num_cached_tokens") is not None:
            metadata_ctx["num_cached_tokens"] = kwargs.get("num_cached_tokens")
        if kwargs.get("operation") is not None:
            metadata_ctx["operation"] = kwargs.get("operation")
        if kwargs.get("lora_name") is not None:
            metadata_ctx["lora_name"] = kwargs.get("lora_name")
        if kwargs.get("request_id") is not None:
            metadata_ctx["request_id"] = kwargs.get("request_id")
        if kwargs.get("embedding_dim") is not None:
            metadata_ctx["embedding_dim"] = kwargs.get("embedding_dim")
        # Embedding-specific metadata (if provided by caller)
        if kwargs.get("encoding_format") is not None:
            metadata_ctx["encoding_format"] = kwargs.get("encoding_format")

        # Token metrics: always include keys to avoid 'None' in UI for embeddings
        input_tokens = int(kwargs.get("input_tokens", 0) or 0)
        output_tokens = int(kwargs.get("output_tokens", 0) or 0)
        metrics_ctx: Dict[str, Any] = {
            INPUT_TOKENS_METRIC_KEY: input_tokens,
            OUTPUT_TOKENS_METRIC_KEY: output_tokens,
            TOTAL_TOKENS_METRIC_KEY: input_tokens + output_tokens,
        }

        # Model fields
        provider = "vllm"
        short_model = model_name or ""
        if short_model and "/" in short_model:
            provider, short_model = short_model.split("/", 1)

        span_kind = "embedding" if (operation == "embedding" or kwargs.get("operation") == "embedding") else "llm"
        ctx_items: Dict[str, Any] = {
            SPAN_KIND: span_kind,
            MODEL_NAME: short_model or (model_name or ""),
            MODEL_PROVIDER: provider,
            METADATA: metadata_ctx,
            METRICS: metrics_ctx,
        }
        # Prefer prompt provided by caller; no per-request caching.
        prompt = kwargs.get("prompt")
        if span_kind == "embedding":
            # Prefer OpenAI-like 'input' semantics
            embedding_inputs = kwargs.get("input")
            try:
                docs: list[Document] = []
                if embedding_inputs is not None:
                    # Normalize to list like OpenAI integration
                    if isinstance(embedding_inputs, str) or (
                        isinstance(embedding_inputs, list)
                        and len(embedding_inputs) > 0
                        and isinstance(embedding_inputs[0], int)
                    ):
                        embedding_inputs = [embedding_inputs]
                    if isinstance(embedding_inputs, list):
                        for doc in embedding_inputs:
                            docs.append(Document(text=str(doc)))
                elif prompt is not None:
                    # Fallback to single prompt representation
                    docs.append(Document(text=str(prompt)))
                if docs:
                    ctx_items[INPUT_DOCUMENTS] = docs
            except Exception:
                ctx_items[INPUT_DOCUMENTS] = [Document(text="[unavailable]")]

            emb_dim = kwargs.get("embedding_dim")
            # Compute number of embeddings returned
            num_embeddings = kwargs.get("num_embeddings")
            if not isinstance(num_embeddings, int) or num_embeddings <= 0:
                try:
                    if isinstance(embedding_inputs, list):
                        num_embeddings = len(embedding_inputs)
                    else:
                        num_embeddings = 1
                except Exception:
                    num_embeddings = 1

            if emb_dim is not None:
                ctx_items[OUTPUT_VALUE] = f"[{num_embeddings} embedding(s) returned with size {emb_dim}]"
            else:
                ctx_items[OUTPUT_VALUE] = f"[{num_embeddings} embedding(s) returned]"
        else:
            # Completion-style tagging
            if prompt:
                ctx_items[INPUT_MESSAGES] = [{"content": prompt}]
            output_text = kwargs.get("output_text")
            if output_text:
                ctx_items[OUTPUT_MESSAGES] = [{"content": output_text}]

        # Set supplemental span tags for convenient querying
        try:
            if kwargs.get("operation"):
                span.set_tag_str("vllm.request.operation", str(kwargs.get("operation")))
            if kwargs.get("finish_reason"):
                span.set_tag_str("vllm.request.finish_reason", str(kwargs.get("finish_reason")))
            if kwargs.get("stop_reason") is not None:
                span.set_tag_str("vllm.request.stop_reason", str(kwargs.get("stop_reason")))
            if kwargs.get("lora_name"):
                span.set_tag_str("vllm.request.lora_name", str(kwargs.get("lora_name")))
            if kwargs.get("request_id"):
                span.set_tag_str("vllm.request.id", str(kwargs.get("request_id")))
        except Exception:
            pass

        log.debug("[VLLM INTEGRATION DEBUG] Setting ctx_items (V1): %s", ctx_items)
        span._set_ctx_items(ctx_items)
        log.debug("[VLLM INTEGRATION DEBUG] LLMObs tags set successfully (V1)")
 
