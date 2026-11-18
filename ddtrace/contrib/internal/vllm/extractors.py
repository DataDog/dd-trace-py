from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Optional

from ._constants import ATTR_MODEL_NAME


if TYPE_CHECKING:
    from vllm.v1.engine.core import EngineCoreOutput
    from vllm.v1.engine.output_processor import RequestState
    from vllm.v1.stats import RequestStateStats


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


@dataclass
class LatencyMetrics:
    """Computed latency metrics from vLLM RequestStateStats."""

    time_to_first_token: Optional[float] = None
    time_in_queue: Optional[float] = None
    time_in_model_prefill: Optional[float] = None
    time_in_model_decode: Optional[float] = None
    time_in_model_inference: Optional[float] = None


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


def extract_request_data(req_state: RequestState, engine_core_output: EngineCoreOutput) -> RequestData:
    """Extract request data from engine-side structures.

    Args:
        req_state: RequestState from OutputProcessor.request_states
        engine_core_output: EngineCoreOutput from engine_core

    Returns:
        RequestData for LLMObs tagging
    """
    is_embedding = engine_core_output.pooling_output is not None

    # Get prompt text - if not available, decode from token IDs (but not for embeddings)
    # skip_special_tokens=False preserves chat template markers for parsing
    prompt_text = req_state.prompt
    if not is_embedding and prompt_text is None and req_state.prompt_token_ids and req_state.detokenizer:
        tokenizer = getattr(req_state.detokenizer, "tokenizer", None)
        if tokenizer:
            prompt_text = tokenizer.decode(req_state.prompt_token_ids, skip_special_tokens=False)

    data = RequestData(
        prompt=prompt_text,
        input_tokens=req_state.prompt_len or 0,
        lora_name=req_state.lora_name,
        num_cached_tokens=get_num_cached_tokens(req_state, engine_core_output),
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


def get_num_cached_tokens(req_state: RequestState, engine_core_output: EngineCoreOutput) -> int:
    """Extract the number of prefix-cached prompt tokens.

    vLLM < 0.20 sets num_cached_tokens on every EngineCoreOutput. vLLM >= 0.20 sets it in
    prefill_stats on the prefill step output only, and then copies it to req_state.
    The prefill step can also be the final step (pooling, single-token generation), so read
    prefill_stats first, because req_state is not updated yet at capture time.
    With disaggregated prefill, vLLM >= 0.31 overrides the count with remote_prefill_cached_tokens,
    the cache hits on the remote prefill worker. vLLM sets it when it creates the request state.
    """
    num_cached_tokens = getattr(engine_core_output, "num_cached_tokens", None)
    if num_cached_tokens is not None:
        return int(num_cached_tokens)

    remote_prefill_cached_tokens = getattr(req_state, "remote_prefill_cached_tokens", None)
    if remote_prefill_cached_tokens is not None:
        return int(remote_prefill_cached_tokens)

    prefill_stats = getattr(engine_core_output, "prefill_stats", None)
    if prefill_stats is not None:
        return int(prefill_stats.num_cached_tokens)

    return int(getattr(req_state, "num_cached_tokens", 0) or 0)


def get_model_name(instance) -> Optional[str]:
    """Extract injected model name (set by traced_engine_init)"""
    return getattr(instance, ATTR_MODEL_NAME, None)


def extract_latency_metrics(stats: Optional[RequestStateStats]) -> Optional[LatencyMetrics]:
    """Extract latency metrics from vLLM RequestStateStats.

    Single source of truth for latency calculation logic.
    """
    if not stats:
        return None

    metrics = LatencyMetrics()

    if stats.first_token_latency:
        metrics.time_to_first_token = float(stats.first_token_latency)

    queued = stats.queued_ts
    scheduled = stats.scheduled_ts
    first_token = stats.first_token_ts
    last_token = stats.last_token_ts

    if queued and scheduled:
        metrics.time_in_queue = float(scheduled - queued)

    if scheduled and first_token:
        metrics.time_in_model_prefill = float(first_token - scheduled)

    if first_token and last_token and last_token > first_token:
        metrics.time_in_model_decode = float(last_token - first_token)

    if scheduled and last_token:
        metrics.time_in_model_inference = float(last_token - scheduled)

    return metrics
