"""Data extraction helpers isolated from wrapping logic."""

from __future__ import annotations

import torch

from dataclasses import dataclass
from typing import Any, List, Optional

from vllm.outputs import PoolingRequestOutput, RequestOutput
from vllm.sequence import SequenceGroup, Sequence


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


def extract_v0_data(res: RequestOutput, seq_group: SequenceGroup) -> RequestData:
    data = RequestData(
        request_id=res.request_id,
        num_cached_tokens=res.num_cached_tokens,
    )

    # prompt
    data.prompt = res.prompt or seq_group.prompt or seq_group.encoder_prompt

    # input tokens (prefer seq_group)
    sg_prompt_ids = seq_group.prompt_token_ids or []
    sg_enc_prompt_ids = seq_group.encoder_prompt_token_ids or []
    data.input_tokens = len(sg_prompt_ids) + len(sg_enc_prompt_ids)
    if data.input_tokens == 0:
        pt = res.prompt_token_ids or []
        ept = res.encoder_prompt_token_ids or []
        data.input_tokens = len(pt) + len(ept)

    data.sampling_params = seq_group.sampling_params
    lora_req = seq_group.lora_request
    data.lora_name = lora_req.name if lora_req else None

    if isinstance(res, PoolingRequestOutput):
        out: torch.Tensor = res.outputs.data
        shape = out.shape
        if shape:
            data.embedding_dim = int(shape[-1])
        return data

    # completion path
    seqs: list[Sequence] = seq_group.get_seqs() or []
    out_txt: list[str] = []
    for s in seqs:
        if s.get_output_len():
            data.output_tokens += int(s.get_output_len())
        text = s.output_text
        if text:
            out_txt.append(text)
    data.output_text = "".join(out_txt)

    for comp in res.outputs or []:
        if data.output_tokens == 0:
            token_ids = comp.token_ids
            if token_ids:
                data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
        if not data.finish_reason:
            data.finish_reason = comp.finish_reason
        if data.stop_reason is None:
            data.stop_reason = comp.stop_reason

    return data


def extract_v1_streaming_data(outputs: List[RequestOutput]) -> RequestData:
    data = RequestData()

    for out in outputs:
        data.prompt = data.prompt or out.prompt or out.encoder_prompt
        if not data.input_tokens:
            pt = out.prompt_token_ids
            if pt:
                data.input_tokens = len(pt)

        if data.num_cached_tokens is None:
            data.num_cached_tokens = out.num_cached_tokens

        for comp in out.outputs or []:
            text = comp.text
            if text:
                data.output_text += text
            token_ids = comp.token_ids
            if token_ids:
                data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
            fr = comp.finish_reason
            if fr:
                data.finish_reason = fr
            if data.stop_reason is None:
                data.stop_reason = comp.stop_reason

    return data


def extract_offline_data(request_output: RequestOutput, prompts=None, model_name=None) -> RequestData:
    data = RequestData(
        request_id=request_output.request_id,
        prompt=request_output.prompt or (prompts if isinstance(prompts, str) else None),
        model_name=model_name,
        num_cached_tokens=request_output.num_cached_tokens,
        input_tokens=len(request_output.prompt_token_ids or []),
    )

    parts = []
    for comp in request_output.outputs or []:
        text = comp.text
        if text:
            parts.append(text)
        token_ids = comp.token_ids
        if token_ids:
            data.output_tokens += len(token_ids) if isinstance(token_ids, list) else 1
        if not data.finish_reason:
            data.finish_reason = comp.finish_reason
        if data.stop_reason is None:
            data.stop_reason = comp.stop_reason

    data.output_text = "".join(parts)

    lora_req = request_output.lora_request
    data.lora_name = lora_req.name if lora_req else None
    return data


def extract_captured_prompt(parent_span) -> Optional[str]:
    return parent_span._get_ctx_item("vllm.captured_prompt") if parent_span else None


def extract_model_name(instance: Any) -> Optional[str]:
    mc = instance.model_config
    return mc.model if mc else None


def extract_lora_name(lora_request: Any) -> Optional[str]:
    if not lora_request:
        return None
    return lora_request.lora_name or lora_request.name
