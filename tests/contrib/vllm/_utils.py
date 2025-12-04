from __future__ import annotations

import gc

import torch
from vllm.engine.arg_utils import AsyncEngineArgs


def create_async_engine(model: str, *, engine_mode: str = "0", **kwargs):
    """Create an async engine (V0 or V1) with auto-tuned GPU memory utilization."""
    gpu_util = kwargs.pop("gpu_memory_utilization", None)
    gpu_util_candidates = [gpu_util] if gpu_util else [0.1, 0.2, 0.3, 0.5]

    for util in gpu_util_candidates:
        try:
            args = AsyncEngineArgs(model=model, gpu_memory_utilization=util, **kwargs)
            if engine_mode == "1":
                from vllm.v1.engine.async_llm import AsyncLLM

                return AsyncLLM.from_engine_args(args)
            else:
                from vllm.engine.async_llm_engine import AsyncLLMEngine

                return AsyncLLMEngine.from_engine_args(args)
        except Exception as exc:
            last_error = exc
            continue
    raise last_error  # type: ignore[possibly-unbound]


def get_simple_chat_template() -> str:
    """Return a ChatML-style template for testing."""
    return (
        "{% for message in messages %}"
        "<|im_start|>{{ message['role'] }}\n{{ message['content'] }}<|im_end|>\n"
        "{% endfor %}"
        "<|im_start|>assistant\n"
    )


def shutdown_cached_llms() -> None:
    """Free GPU memory after tests."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
