from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING
from typing import List
from typing import Optional

from ddtrace.llmobs.types import Message

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


def extract_request_data(req_state: "RequestState", engine_core_output: "EngineCoreOutput") -> RequestData:
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
    return getattr(instance, ATTR_MODEL_NAME, None)


def extract_latency_metrics(stats: Optional["RequestStateStats"]) -> Optional[LatencyMetrics]:
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


# Role patterns: (quick_check_marker, regex_pattern)
# Quick check avoids running all regexes - we first check if marker exists in prompt
_ROLE_PATTERNS = [
    # Llama 3: <|start_header_id|>role<|end_header_id|>
    ("<|start_header_id|>", re.compile(r"<\|start_header_id\|>(system|user|assistant)<\|end_header_id\|>")),
    # Llama 4: <|header_start|>role<|header_end|>
    ("<|header_start|>", re.compile(r"<\|header_start\|>(system|user|assistant)<\|header_end\|>")),
    # Granite: <|start_of_role|>role<|end_of_role|> (includes document/documents roles)
    ("<|start_of_role|>", re.compile(r"<\|start_of_role\|>(system|user|assistant|documents?)<\|end_of_role\|>")),
    # Gemma: <start_of_turn>role (uses "model" for assistant)
    ("<start_of_turn>", re.compile(r"<start_of_turn>(system|user|model)")),
    # MiniMax: <beginning_of_sentence>role
    ("<beginning_of_sentence>", re.compile(r"<beginning_of_sentence>(system|user|ai)")),
    # ChatML/Qwen/Hermes: <|im_start|>role
    ("<|im_start|>", re.compile(r"<\|im_start\|>(system|user|assistant)")),
    # DeepSeek VL2: <|User|>: / <|Assistant|>: (normal | with colon)
    ("<|User|>:", re.compile(r"<\|(User|Assistant)\|>:")),
    # Phi: <|system|> / <|user|> / <|assistant|>
    ("<|system|>", re.compile(r"<\|(system|user|assistant)\|>")),
    ("<|user|>", re.compile(r"<\|(system|user|assistant)\|>")),
    # DeepSeek V3 (fullwidth ｜ - U+FF5C, not the same as |): <｜User｜>
    ("<｜", re.compile(r"<｜(User|Assistant)｜>")),
    # TeleFLM: <_role>
    ("<_user>", re.compile(r"<_(system|user|bot)>")),
    ("<_system>", re.compile(r"<_(system|user|bot)>")),
    # Inkbot: <#role#>
    ("<#user#>", re.compile(r"<#(system|user|bot)#>")),
    ("<#system#>", re.compile(r"<#(system|user|bot)#>")),
    # Alpaca: ### Instruction: / ### Response:
    ("### Instruction", re.compile(r"### (Instruction|Response|Input):")),
    # Falcon: Role: prefix (capitalized)
    ("User:", re.compile(r"^(System|User|Assistant|Falcon): ?", re.MULTILINE)),
]


# End-of-turn markers to strip (case-sensitive)
_END_MARKERS = re.compile(
    r"<\|im_end\|>|<\|eot_id\|>|<\|end\|>|<\|eot\|>|<\|eom\|>|"
    r"<\|end_of_text\|>|<end_of_turn>|<end_of_sentence>|<\|eos\|>|"
    r"<｜end▁of▁sentence｜>",
)

# Roles that represent the "assistant" in various templates
_ASSISTANT_ROLES = ("assistant", "model", "ai", "bot", "response", "falcon")


def parse_prompt_to_messages(prompt: Optional[str]) -> List[Message]:
    """Parse a formatted prompt into structured messages."""
    if not prompt:
        return []

    # Quick check: find first matching marker, then use its pattern
    for marker, pattern in _ROLE_PATTERNS:
        if marker in prompt:
            messages = _parse_with_pattern(prompt, pattern)
            if messages:
                return messages

    # No recognized format - return raw prompt as single message
    return [Message(role="", content=prompt)]


def _parse_with_pattern(prompt: str, role_pattern) -> List[Message]:
    """Parse prompt using a specific role pattern."""
    matches = list(role_pattern.finditer(prompt))
    if not matches:
        return []

    messages: List[Message] = []
    for i, match in enumerate(matches):
        role_match = match.group(1)
        if not role_match:
            continue
        role = role_match.lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(prompt)
        content = _END_MARKERS.sub("", prompt[start:end]).lstrip(":").strip()

        # Skip empty trailing assistant-like roles (generation prompt marker)
        if role in _ASSISTANT_ROLES and not content and i == len(matches) - 1:
            continue

        messages.append(Message(role=role, content=content))

    return messages
