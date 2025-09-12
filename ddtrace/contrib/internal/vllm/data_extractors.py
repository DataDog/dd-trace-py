"""Data extraction helpers isolated from wrapping logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

import torch
from vllm.outputs import CompletionOutput, PoolingRequestOutput, RequestOutput
from vllm.sequence import Sequence, SequenceGroup


# ---------- data container ---------------------------------------------------

@dataclass
class RequestData:
    request_id: Optional[str] = None
    prompt: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    output_text: str = ""
    finish_reason: Optional[str] = None
    stop_reason: Optional[Any] = None
    embedding_dim: Optional[int] = None
    num_cached_tokens: Optional[int] = None
    model_name: Optional[str] = None
    lora_name: Optional[str] = None
    sampling_params: Optional[Any] = None


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



def _lora_name(lora_req: Any) -> Optional[str]:
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

def extract_v0_data(res: RequestOutput, seq_group: SequenceGroup) -> RequestData:
    data = RequestData(
        request_id=res.request_id,
        num_cached_tokens=res.num_cached_tokens,
    )

    # Prompt (prefer response, then seq_group)
    data.prompt = _first_non_empty(res.prompt, seq_group.prompt, seq_group.encoder_prompt)

    # Input tokens (prefer seq_group; fall back to response)
    data.input_tokens = _len_or_zero(seq_group.prompt_token_ids) + _len_or_zero(seq_group.encoder_prompt_token_ids)
    if data.input_tokens == 0:
        data.input_tokens = _len_or_zero(res.prompt_token_ids) + _len_or_zero(res.encoder_prompt_token_ids)

    data.sampling_params = seq_group.sampling_params
    data.lora_name = _lora_name(seq_group.lora_request)

    # Embedding path
    if isinstance(res, PoolingRequestOutput):
        data.embedding_dim = _embedding_dim(res.outputs.data)
        return data

    # Completion path: prefer seqs (accurate for DELTA mode), then supplement from outputs
    seqs: list[Sequence] = seq_group.get_seqs() or []
    if seqs:
        text_parts: list[str] = []
        for s in seqs:
            out_len = s.get_output_len()
            if out_len:
                data.output_tokens += int(out_len)
            if s.output_text:
                text_parts.append(s.output_text)
        if text_parts:
            data.output_text = "".join(text_parts)

    _accumulate_completion_outputs(
        res.outputs,
        data,
        accumulate_text=False,          # text already built from seqs
        add_tokens_if_empty=True,       # only if seq-based count was 0
    )
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


def extract_offline_data(request_output: RequestOutput, prompts=None, model_name=None) -> RequestData:
    data = RequestData(
        request_id=request_output.request_id,
        prompt=_first_non_empty(request_output.prompt, prompts if isinstance(prompts, str) else None),
        model_name=model_name,
        num_cached_tokens=request_output.num_cached_tokens,
        input_tokens=_len_or_zero(request_output.prompt_token_ids),
    )

    _accumulate_completion_outputs(request_output.outputs, data)
    data.lora_name = _lora_name(request_output.lora_request)
    return data


# ---------- tiny lookups used by wrappers -----------------------------------

def extract_captured_prompt(parent_span) -> Optional[str]:
    return parent_span._get_ctx_item("vllm.captured_prompt") if parent_span else None


def extract_model_name(instance: Any) -> Optional[str]:
    mc = instance.model_config
    return mc.model if mc else None


def extract_lora_name(lora_request: Any) -> Optional[str]:
    return _lora_name(lora_request)
