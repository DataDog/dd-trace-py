import functools
import inspect
import sys
from typing import Any
from typing import Callable
from typing import Generator
from typing import Optional

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.llama_index._streaming import handle_streamed_response
from ddtrace.contrib.internal.llama_index._utils import build_agent_call_tool_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_agent_run_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_chat_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_complete_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_predict_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_query_embedding_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_query_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_text_embedding_batch_request_kwargs
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import LlamaIndexIntegration


log = get_logger(__name__)


def _llama_core():
    # AIDEV-NOTE: Cannot import llama_index.core at module level — Pydantic V2 import
    # machinery conflicts with ddtrace-run's import hook.
    return sys.modules.get("llama_index.core") or __import__("llama_index.core", fromlist=["core"])


def get_version() -> str:
    return getattr(_llama_core(), "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"llama_index.core": ">=0.11.0"}


config._add("llama_index", {})


_originals: dict[tuple[type, str], Any] = {}
_wrapped_classes: set[type] = set()
_integration = None
_DD_WRAPPED = "__dd_wrapped__"


# AIDEV-NOTE: We use functools.wraps-based monkey-patching instead of wrapt because
# LlamaIndex classes inherit from Pydantic V2 BaseModel, which conflicts with wrapt's
# BoundFunctionWrapper descriptors causing TypeError during model instantiation.


def _make_wrapper(wrapper_fn, original_fn):
    """Replace *original_fn* with a wrapper that delegates to *wrapper_fn*."""
    if inspect.iscoroutinefunction(wrapper_fn):

        @functools.wraps(original_fn)
        async def wrapper(self, *args, **kwargs):
            return await wrapper_fn(original_fn.__get__(self, type(self)), self, args, kwargs)
    else:

        @functools.wraps(original_fn)
        def wrapper(self, *args, **kwargs):
            return wrapper_fn(original_fn.__get__(self, type(self)), self, args, kwargs)

    wrapper.__dd_wrapped__ = original_fn
    return wrapper


def _create_event(
    instance: Any,
    func: Callable[..., Any],
    request_kwargs: dict[str, Any],
    model: Optional[str] = None,
    is_chat: Optional[bool] = None,
    operation: str = "",
) -> LlmRequestEvent:
    return LlmRequestEvent(
        component="llama_index",
        service=int_service(None, _integration.integration_config),
        resource=f"{instance.__class__.__name__}.{func.__name__}",
        provider="llama_index",
        model=model,
        llmobs_integration=_integration,
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
        instance=instance,
        is_chat=is_chat,
        operation=operation,
    )


# ---------------------------------------------------------------------------
# Trace runners — one pair for model calls (LLM + embeddings: needs model,
# is_chat, streaming) and one pair for non-model operations (needs operation).
# ---------------------------------------------------------------------------


def _traced_llm(func, instance, args, kwargs, request_kwargs, model, is_chat, always_stream=False):
    """Trace a sync LLM/embedding call (chat, complete, predict, stream, embedding variants)."""
    event = _create_event(instance, func, request_kwargs, model=model, is_chat=is_chat)
    # AIDEV-NOTE: dispatch_end_event=False defers the ended event for streaming.
    # Non-streaming calls dispatch_ended_event() immediately; streaming defers
    # to the stream handler's finalize_stream().
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        if always_stream or isinstance(resp, Generator):
            return handle_streamed_response(_integration, resp, args, request_kwargs, ctx, is_chat=is_chat)
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


async def _traced_llm_async(func, instance, args, kwargs, request_kwargs, model, is_chat, always_stream=False):
    """Trace an async LLM/embedding call (achat, acomplete, apredict, astream, aembedding variants)."""
    event = _create_event(instance, func, request_kwargs, model=model, is_chat=is_chat)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        if always_stream or inspect.isasyncgen(resp):
            return handle_streamed_response(_integration, resp, args, request_kwargs, ctx, is_chat=is_chat)
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


def _traced_operation(func, instance, args, kwargs, request_kwargs, operation):
    """Trace a sync non-model operation (query, retrieve, agent)."""
    event = _create_event(instance, func, request_kwargs, operation=operation)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


async def _traced_operation_async(func, instance, args, kwargs, request_kwargs, operation):
    """Trace an async non-model operation (aquery, aretrieve, agent)."""
    event = _create_event(instance, func, request_kwargs, operation=operation)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        event.response = resp
        ctx.dispatch_ended_event()
        return resp


# ---------------------------------------------------------------------------
# Wrapper factories — generate sync/async wrappers for the trace runners.
# ---------------------------------------------------------------------------


def _llm_wrapper(build_kwargs_fn, is_chat, always_stream=False):
    def wrapper(func, instance, args, kwargs):
        request_kwargs, model = build_kwargs_fn(instance, args, kwargs)
        return _traced_llm(func, instance, args, kwargs, request_kwargs, model, is_chat, always_stream)

    return wrapper


def _llm_wrapper_async(build_kwargs_fn, is_chat, always_stream=False):
    async def wrapper(func, instance, args, kwargs):
        request_kwargs, model = build_kwargs_fn(instance, args, kwargs)
        return await _traced_llm_async(func, instance, args, kwargs, request_kwargs, model, is_chat, always_stream)

    return wrapper


def _operation_wrapper(build_kwargs_fn, operation):
    def wrapper(func, instance, args, kwargs):
        return _traced_operation(func, instance, args, kwargs, build_kwargs_fn(args, kwargs), operation)

    return wrapper


def _operation_wrapper_async(build_kwargs_fn, operation):
    async def wrapper(func, instance, args, kwargs):
        return await _traced_operation_async(func, instance, args, kwargs, build_kwargs_fn(args, kwargs), operation)

    return wrapper


# ---------------------------------------------------------------------------
# Method-to-wrapper mappings
# ---------------------------------------------------------------------------

_LLM_WRAPPERS = {
    "chat": _llm_wrapper(build_chat_request_kwargs, is_chat=True),
    "complete": _llm_wrapper(build_complete_request_kwargs, is_chat=False),
    "stream_chat": _llm_wrapper(build_chat_request_kwargs, is_chat=True, always_stream=True),
    "stream_complete": _llm_wrapper(build_complete_request_kwargs, is_chat=False, always_stream=True),
    "predict": _llm_wrapper(build_predict_request_kwargs, is_chat=False),
    "achat": _llm_wrapper_async(build_chat_request_kwargs, is_chat=True),
    "acomplete": _llm_wrapper_async(build_complete_request_kwargs, is_chat=False),
    "astream_chat": _llm_wrapper_async(build_chat_request_kwargs, is_chat=True, always_stream=True),
    "astream_complete": _llm_wrapper_async(build_complete_request_kwargs, is_chat=False, always_stream=True),
    "apredict": _llm_wrapper_async(build_predict_request_kwargs, is_chat=False),
}

_QUERY_ENGINE_WRAPPERS = {
    "query": _operation_wrapper(build_query_request_kwargs, operation="query"),
    "aquery": _operation_wrapper_async(build_query_request_kwargs, operation="query"),
}

_RETRIEVER_WRAPPERS = {
    "retrieve": _operation_wrapper(build_query_request_kwargs, operation="retrieval"),
    "aretrieve": _operation_wrapper_async(build_query_request_kwargs, operation="retrieval"),
}

_EMBEDDING_WRAPPERS = {
    "get_query_embedding": _llm_wrapper(build_query_embedding_request_kwargs, is_chat=None),
    "get_text_embedding_batch": _llm_wrapper(build_text_embedding_batch_request_kwargs, is_chat=None),
    "aget_query_embedding": _llm_wrapper_async(build_query_embedding_request_kwargs, is_chat=None),
    "aget_text_embedding_batch": _llm_wrapper_async(build_text_embedding_batch_request_kwargs, is_chat=None),
}

_AGENT_WRAPPERS = {
    "run": _operation_wrapper(build_agent_run_request_kwargs, operation="agent"),
    "call_tool": _operation_wrapper_async(build_agent_call_tool_request_kwargs, operation="tool"),
}


def _wrap_class(cls, wrappers):
    """Wrap methods on *cls* using plain function replacement."""
    for method_name, wrapper_fn in wrappers.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not hasattr(original, _DD_WRAPPED):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)
    _wrapped_classes.add(cls)


def _all_subclasses(base_cls):
    """Recursively collect all subclasses of *base_cls*."""
    result = set()
    for sub in base_cls.__subclasses__():
        result.add(sub)
        result.update(_all_subclasses(sub))
    return result


def _patched_init(original_init):
    """Wrap BaseLLM.__init__ to patch new subclass methods on first instantiation."""

    @functools.wraps(original_init)
    def wrapper(self, *args, **kwargs):
        result = original_init(self, *args, **kwargs)
        cls = type(self)
        if cls not in _wrapped_classes:
            _wrap_class(cls, _LLM_WRAPPERS)
        return result

    wrapper.__dd_wrapped__ = original_init
    return wrapper


def patch():
    global _integration

    core_mod = _llama_core()
    if getattr(core_mod, "_datadog_patch", False):
        return
    core_mod._datadog_patch = True

    integration = LlamaIndexIntegration(integration_config=config.llama_index)
    core_mod._datadog_integration = integration
    _integration = integration

    # AIDEV-NOTE: Subclass wrapping is required because LlamaIndex LLM methods are abstract on
    # BaseLLM — concrete subclasses (OpenAI, etc.) override them entirely.
    base = core_mod.base
    targets = [
        (base.llms.base.BaseLLM, _LLM_WRAPPERS),
        (base.base_query_engine.BaseQueryEngine, _QUERY_ENGINE_WRAPPERS),
        (base.base_retriever.BaseRetriever, _RETRIEVER_WRAPPERS),
        (base.embeddings.base.BaseEmbedding, _EMBEDDING_WRAPPERS),
    ]
    try:
        from llama_index.core.agent.workflow.base_agent import BaseWorkflowAgent

        targets.append((BaseWorkflowAgent, _AGENT_WRAPPERS))
    except (ImportError, ModuleNotFoundError):
        pass

    for base_cls, wrappers in targets:
        for cls in [base_cls, *_all_subclasses(base_cls)]:
            _wrap_class(cls, wrappers)

    # Hook BaseLLM.__init__ so LLM subclasses created after patch() get wrapped too
    BaseLLM = base.llms.base.BaseLLM
    _originals[(BaseLLM, "__init__")] = BaseLLM.__init__
    BaseLLM.__init__ = _patched_init(BaseLLM.__init__)


def unpatch():
    global _integration

    core_mod = _llama_core()
    if not getattr(core_mod, "_datadog_patch", False):
        return
    core_mod._datadog_patch = False

    for (cls, method_name), original in _originals.items():
        try:
            setattr(cls, method_name, original)
        except Exception:
            log.debug("Failed to unwrap %s.%s", cls.__name__, method_name, exc_info=True)
    _originals.clear()
    _wrapped_classes.clear()

    if hasattr(core_mod, "_datadog_integration"):
        delattr(core_mod, "_datadog_integration")
    _integration = None
