from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RequestData:
    """Container for vLLM request data extracted from engine outputs."""

    prompt: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    output_text: str = ""
    finish_reason: Optional[str] = None
    embedding_dim: Optional[int] = None
    num_embeddings: int = 1
    lora_name: Optional[str] = None
    num_cached_tokens: int = 0
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    max_tokens: Optional[int] = None
    input_: Optional[list[int]] = None


def get_embedding_shape(tensor) -> tuple[int, Optional[int]]:
    """Extract (num_embeddings, embedding_dim) from torch.Tensor."""
    if tensor is None or len(tensor.shape) == 0:
        return 1, None

    if len(tensor.shape) == 1:
        return 1, int(tensor.shape[0])

    first, last = int(tensor.shape[0]), int(tensor.shape[-1])
    if last == 1:
        return 1, first
    return first, last


def extract_request_data(req_state, engine_core_output) -> RequestData:
    """Extract request data from engine-side structures.

    Args:
        req_state: RequestState from OutputProcessor.request_states
        engine_core_output: EngineCoreOutput from engine_core

    Returns:
        RequestData for LLMObs tagging
    """
    is_embedding = engine_core_output.pooling_output is not None

    # Get prompt text - if not available, decode from token IDs (but not for embeddings)
    prompt_text = req_state.prompt
    if not is_embedding and prompt_text is None and req_state.prompt_token_ids and req_state.detokenizer:
        tokenizer = getattr(req_state.detokenizer, "tokenizer", None)
        if tokenizer:
            prompt_text = tokenizer.decode(req_state.prompt_token_ids)

    data = RequestData(
        prompt=prompt_text,
        input_tokens=req_state.prompt_len or 0,
        lora_name=req_state.lora_name,
        num_cached_tokens=engine_core_output.num_cached_tokens,
        temperature=req_state.temperature,
        top_p=req_state.top_p,
        n=req_state.n,
        max_tokens=req_state.max_tokens_param,
    )

    if engine_core_output.finish_reason:
        data.finish_reason = str(engine_core_output.finish_reason)

    if is_embedding:
        num_emb, emb_dim = get_embedding_shape(engine_core_output.pooling_output)
        data.num_embeddings = num_emb
        data.embedding_dim = emb_dim
        data.input_ = req_state.prompt_token_ids
    else:
        # Don't extract output_tokens here - stats not updated yet
        # Will be extracted later from captured stats reference

        if req_state.detokenizer:
            data.output_text = req_state.detokenizer.output_text

    return data


def get_model_name(instance) -> Optional[str]:
    """Extract injected model name (set by traced_engine_init)"""
    return getattr(instance, "_dd_model_name", None)
