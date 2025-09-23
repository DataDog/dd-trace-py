from __future__ import annotations

import os
import vllm


def _create_llm_autotune(model, **kwargs):
    util_candidates = kwargs.pop(
        "gpu_util_candidates",
        [0.1, 0.2, 0.3, 0.5, 0.7, 0.85, 0.9, 0.95],
    )
    last_error = None
    for util in util_candidates:
        try:
            return vllm.LLM(model=model, gpu_memory_utilization=util, **kwargs)
        except Exception as exc:
            last_error = exc
            continue
    raise last_error


_LLM_CACHE = {}


def get_cached_llm(model: str, *, engine_mode: str | None = None, **kwargs):
    # Avoid passing runner=None to vLLM; default runner is 'generate'
    runner = kwargs.get("runner")
    key_runner = runner or "generate"
    mode_key = engine_mode if engine_mode is not None else os.environ.get("VLLM_USE_V1", "0")
    key = (model, key_runner, mode_key)
    llm = _LLM_CACHE.get(key)
    if llm is not None:
        return llm
    llm_kwargs = dict(kwargs)
    if runner is None and "runner" in llm_kwargs:
        llm_kwargs.pop("runner", None)
    llm = _create_llm_autotune(model=model, **llm_kwargs)
    _LLM_CACHE[key] = llm
    return llm


def shutdown_cached_llms() -> None:
    for llm in list(_LLM_CACHE.values()):
        try:
            shutdown = getattr(llm, "shutdown", None)
            if callable(shutdown):
                shutdown()
        except Exception:
            pass
    _LLM_CACHE.clear()


