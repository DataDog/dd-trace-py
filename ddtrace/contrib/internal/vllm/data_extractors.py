"""Data extraction helpers isolated from wrapping logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from vllm.outputs import PoolingRequestOutput, RequestOutput


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


def extract_v0_data(res: RequestOutput, seq_group: Any) -> RequestData:
    data = RequestData(
        request_id=getattr(res, "request_id", None),
        num_cached_tokens=getattr(res, "num_cached_tokens", None),
    )

    # prompt
    data.prompt = getattr(res, "prompt", None) or getattr(seq_group, "prompt", None) or getattr(seq_group, "encoder_prompt", None)

    # input tokens (prefer seq_group)
    sg_prompt_ids = getattr(seq_group, "prompt_token_ids", []) or []
    sg_enc_prompt_ids = getattr(seq_group, "encoder_prompt_token_ids", []) or []
    data.input_tokens = len(sg_prompt_ids) + len(sg_enc_prompt_ids)
    if data.input_tokens == 0:
        pt = getattr(res, "prompt_token_ids", []) or []
        ept = getattr(res, "encoder_prompt_token_ids", []) or []
        data.input_tokens = len(pt) + len(ept)

    data.sampling_params = getattr(seq_group, "sampling_params", None)
    lora_req = getattr(seq_group, "lora_request", None)
    data.lora_name = getattr(lora_req, "name", None) if lora_req else None

    if isinstance(res, PoolingRequestOutput):
        out = getattr(getattr(res, "outputs", None), "data", None)
        shape = getattr(out, "shape", None)
        if shape:
            data.embedding_dim = int(shape[-1])
        return data

    # completion path
    seqs = seq_group.get_seqs() or []
    out_txt = []
    for s in seqs:
        if hasattr(s, "get_output_len"):
            data.output_tokens += int(s.get_output_len())
        text = getattr(s, "output_text", None)
        if text:
            out_txt.append(text)
    data.output_text = "".join(out_txt)

    for comp in getattr(res, "outputs", None) or []:
        if data.output_tokens == 0:
            token_ids = getattr(comp, "token_ids", None)
            if token_ids:
                data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
        if not data.finish_reason:
            data.finish_reason = getattr(comp, "finish_reason", None)
        if data.stop_reason is None:
            data.stop_reason = getattr(comp, "stop_reason", None)

    return data


def extract_v1_streaming_data(outputs: List[RequestOutput]) -> RequestData:
    data = RequestData()

    for out in outputs:
        data.prompt = data.prompt or getattr(out, "prompt", None) or getattr(out, "encoder_prompt", None)
        if not data.input_tokens:
            pt = getattr(out, "prompt_token_ids", None)
            if pt:
                data.input_tokens = len(pt)

        if data.num_cached_tokens is None:
            data.num_cached_tokens = getattr(out, "num_cached_tokens", None)

        for comp in getattr(out, "outputs", None) or []:
            text = getattr(comp, "text", None)
            if text:
                data.output_text += text
            token_ids = getattr(comp, "token_ids", None)
            if token_ids:
                data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
            fr = getattr(comp, "finish_reason", None)
            if fr:
                data.finish_reason = fr
            if data.stop_reason is None:
                data.stop_reason = getattr(comp, "stop_reason", None)

    return data


def extract_offline_data(request_output: RequestOutput, prompts=None, model_name=None) -> RequestData:
    data = RequestData(
        request_id=getattr(request_output, "request_id", None),
        prompt=getattr(request_output, "prompt", None) or (prompts if isinstance(prompts, str) else None),
        model_name=model_name,
        num_cached_tokens=getattr(request_output, "num_cached_tokens", None),
        input_tokens=len(getattr(request_output, "prompt_token_ids", []) or []),
    )

    parts = []
    for comp in getattr(request_output, "outputs", None) or []:
        text = getattr(comp, "text", None)
        if text:
            parts.append(text)
        token_ids = getattr(comp, "token_ids", None)
        if token_ids:
            data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
        if not data.finish_reason:
            data.finish_reason = getattr(comp, "finish_reason", None)
        if data.stop_reason is None:
            data.stop_reason = getattr(comp, "stop_reason", None)

    data.output_text = "".join(parts)

    lora_req = getattr(request_output, "lora_request", None)
    data.lora_name = getattr(lora_req, "name", None) if lora_req else None
    return data


def extract_captured_prompt(parent_span) -> Optional[str]:
    return parent_span._get_ctx_item("vllm.captured_prompt") if parent_span else None


def extract_model_name(instance: Any) -> Optional[str]:
    mc = getattr(instance, "model_config", None)
    return getattr(mc, "model", None) if mc else None


def extract_lora_name(lora_request: Any) -> Optional[str]:
    if not lora_request:
        return None
    return getattr(lora_request, "lora_name", None) or getattr(lora_request, "name", None)
