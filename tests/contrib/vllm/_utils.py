from __future__ import annotations

import vllm
from vllm.engine.arg_utils import AsyncEngineArgs


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


def get_cached_llm(model: str, *, engine_mode: str = "0", **kwargs):
    # Avoid passing runner=None to vLLM; default runner is 'generate'
    runner = kwargs.get("runner")
    key_runner = runner or "generate"
    key = (model, key_runner, engine_mode)
    llm = _LLM_CACHE.get(key)
    if llm is not None:
        return llm
    llm_kwargs = dict(kwargs)
    if runner is None and "runner" in llm_kwargs:
        llm_kwargs.pop("runner", None)
    llm = _create_llm_autotune(model=model, **llm_kwargs)
    _LLM_CACHE[key] = llm
    return llm


def create_async_engine(model: str, *, engine_mode: str = "0", **kwargs):
    # Respect explicit gpu_memory_utilization if provided
    explicit_util = kwargs.pop("gpu_memory_utilization", None)
    util_candidates = kwargs.pop(
        "gpu_util_candidates",
        [0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95],
    )
    if explicit_util is not None:
        util_candidates = [explicit_util]
    last_error = None
    for util in util_candidates:
        try:
            args = AsyncEngineArgs(model=model, gpu_memory_utilization=util, **kwargs)
            if engine_mode == "1":
                from vllm.v1.engine.async_llm import AsyncLLM as _Async  # type: ignore
            else:
                from vllm.engine.async_llm_engine import AsyncLLMEngine as _Async  # type: ignore
            engine = _Async.from_engine_args(args)
            return engine
        except Exception as exc:  # pragma: no cover
            last_error = exc
            continue
    raise last_error


def get_simple_chat_template() -> str:
    return (
        "{% for message in messages %}"
        "{% if message['role'] == 'system' %}{{ message['content'] }}\n"
        "{% elif message['role'] == 'user' %}User: {{ message['content'] }}\n"
        "{% elif message['role'] == 'assistant' %}Assistant: {{ message['content'] }}\n"
        "{% endif %}"
        "{% endfor %}"
        "Assistant:"
    )


def shutdown_cached_llms() -> None:
    for llm in list(_LLM_CACHE.values()):
        try:
            shutdown = getattr(llm, "shutdown", None)
            if callable(shutdown):
                shutdown()
        except Exception:
            pass
    _LLM_CACHE.clear()
