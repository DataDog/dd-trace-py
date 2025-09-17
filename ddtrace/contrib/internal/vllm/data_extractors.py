"""Data extraction helpers isolated from wrapping logic."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

import torch
from vllm import SamplingParams
from vllm.outputs import CompletionOutput, PoolingRequestOutput, RequestOutput
from vllm.sequence import Sequence, SequenceGroup, SequenceStatus
from vllm.lora.request import LoRARequest
from ddtrace.trace import Span


# ---------- data container ---------------------------------------------------
@dataclass
class RequestData:
    prompt: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    output_text: str = ""
    finish_reason: Optional[str] = None
    stop_reason: Optional[Any] = None
    embedding_dim: Optional[int] = None
    num_cached_tokens: Optional[int] = None
    lora_name: Optional[str] = None
    sampling_params: Optional[SamplingParams] = None
    input_: Optional[Any] = None
    num_embeddings: Optional[int] = None


# ---------- small utilities --------------------------------------------------
def _first_non_empty(*vals: Optional[Any]) -> Optional[Any]:
    """Return the first value that is not falsy (''/0/None/[])."""
    for v in vals:
        if v:
            return v
    return None


def _len_or_zero(xs: Optional[Iterable[Any]]) -> int:
    return len(xs) if xs else 0


def _embedding_dim(tensor: torch.Tensor) -> Optional[int]:
    if tensor is None:
        return None
    shape = tensor.shape
    return int(shape[-1]) if len(shape) >= 1 else None


def _embedding_shape_info(tensor: torch.Tensor) -> tuple[Optional[int], Optional[int]]:
    """Return (num_embeddings, embedding_dim) for a tensor.
    - For 1-D tensors: (1, length)
    - For >=2-D tensors: if last_dim == 1, treat as a single vector of size first_dim
      (useful for reward heads returning shape (N, 1)); otherwise (first_dim, last_dim)
    """
    if tensor is None:
        return None, None
    shape = tensor.shape
    if len(shape) == 1:
        return 1, int(shape[0])
    if len(shape) >= 2:
        first_dim = int(shape[0])
        last_dim = int(shape[-1])
        if last_dim == 1:
            return 1, first_dim
        return first_dim, last_dim
    return None, None


def _lora_name(lora_req: Optional[LoRARequest]) -> Optional[str]:
    if not lora_req:
        return None
    return lora_req.lora_name or lora_req.name


# ---------- shared accumulation ---------------------------------------------
def _accumulate_completion_outputs(
    outputs: list[CompletionOutput] | None,
    data: RequestData,
    *,
    accumulate_text: bool = True,
    add_tokens_if_empty: bool = False,
    prefer_last_finish_reason: bool = False,
) -> None:
    if not outputs:
        return

    parts: list[str] = []
    for comp in outputs:
        if accumulate_text and comp.text:
            parts.append(comp.text)

        if comp.token_ids:
            token_count = len(comp.token_ids) if isinstance(comp.token_ids, list) else 1
            if add_tokens_if_empty:
                if data.output_tokens == 0:
                    data.output_tokens += token_count
            else:
                data.output_tokens += token_count

        if comp.finish_reason:
            if prefer_last_finish_reason or not data.finish_reason:
                data.finish_reason = comp.finish_reason

        if data.stop_reason is None:
            data.stop_reason = comp.stop_reason

    if accumulate_text and parts:
        data.output_text += "".join(parts)


# ---------- extractors -------------------------------------------------------
def extract_v0_data(seq_group: SequenceGroup) -> RequestData:
    data = RequestData(
        prompt=(seq_group.trace_headers or {}).get("x-datadog-captured-prompt") or seq_group.prompt,
        input_tokens=len(seq_group.prompt_token_ids),
        sampling_params=seq_group.sampling_params,
        lora_name=_lora_name(seq_group.lora_request),
    )

    # Embedding path
    if seq_group.pooling_params is not None:
        num_emb, emb_dim = _embedding_shape_info(seq_group.pooled_data)
        data.embedding_dim = emb_dim
        data.num_embeddings = num_emb
        return data

    # Completion path
    out_txt = []
    for s in seq_group.get_finished_seqs():
        data.output_tokens += int(s.get_output_len())
        if s.output_text:
            out_txt.append(s.output_text)
    data.output_text = "".join(out_txt)
    
    if seq_group.is_finished():
        finished_seqs = seq_group.get_finished_seqs()
        if finished_seqs:
            data.finish_reason = SequenceStatus.get_finished_reason(finished_seqs[0].status)

    return data


def extract_v1_streaming_data(outputs: List[RequestOutput]) -> RequestData:
    data = RequestData()

    for out in outputs:
        data.prompt = data.prompt or _first_non_empty(out.prompt, out.encoder_prompt)

        if not data.input_tokens and out.prompt_token_ids:
            data.input_tokens = len(out.prompt_token_ids)

        if data.num_cached_tokens is None:
            data.num_cached_tokens = out.num_cached_tokens

        _accumulate_completion_outputs(out.outputs, data, prefer_last_finish_reason=True)

    return data


def extract_offline_data(request_output: RequestOutput, prompts=None) -> RequestData:
    data = RequestData(
        prompt=_first_non_empty(request_output.prompt, prompts if isinstance(prompts, str) else None),
        num_cached_tokens=request_output.num_cached_tokens,
        input_tokens=_len_or_zero(request_output.prompt_token_ids),
    )

    _accumulate_completion_outputs(request_output.outputs, data)
    data.lora_name = _lora_name(request_output.lora_request)
    return data


# New: offline pooling extractor (V1 LLM.encode and friends)
def extract_offline_pooling_data(request_output: PoolingRequestOutput, prompts=None) -> RequestData:
    data = RequestData(
        prompt=_first_non_empty(getattr(request_output, "prompt", None), prompts if isinstance(prompts, str) else None),
        input_tokens=_len_or_zero(getattr(request_output, "prompt_token_ids", None)),
    )

    # Determine embedding dimension if present
    outputs = getattr(request_output, "outputs", None)
    num_emb = None
    emb_dim = None
    if outputs is not None:
        # PoolingRequestOutput.outputs could be a structure with .data tensor
        maybe_tensor = getattr(outputs, "data", None)
        num_emb, emb_dim = _embedding_shape_info(maybe_tensor)
    if emb_dim is not None:
        data.embedding_dim = emb_dim
    if num_emb is not None:
        data.num_embeddings = num_emb

    # Set inputs when only token IDs are available
    prompt_token_ids = getattr(request_output, "prompt_token_ids", None)
    if prompt_token_ids and not data.prompt:
        data.input_ = list(prompt_token_ids)

    return data

# ---------- tiny lookups used by wrappers -----------------------------------
def extract_captured_prompt(parent_span: Optional[Span]) -> Optional[str]:
    return parent_span._get_ctx_item("vllm.captured_prompt") if parent_span else None


def extract_model_name(instance: Any) -> Optional[str]:
    """Extract model name from any vLLM engine instance (LLMEngine, AsyncLLMEngine, MQLLMEngineClient)."""
    model_config = getattr(instance, "model_config", None)
    if model_config and hasattr(model_config, "model"):
        return model_config.model
    return None


def extract_lora_name(lora_request: Optional[LoRARequest]) -> Optional[str]:
    return _lora_name(lora_request)