import functools
import inspect
import sys
from typing import Any
from typing import Callable
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
from ddtrace.contrib.internal.llama_index._utils import is_async_generator
from ddtrace.contrib.internal.llama_index._utils import is_generator
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


def _make_wrapper(wrapper_fn, original_fn, wrapper_args=()):
    """Replace *original_fn* with a wrapper that delegates to *wrapper_fn*."""
    if inspect.iscoroutinefunction(wrapper_fn):

        @functools.wraps(original_fn)
        async def wrapper(self, *args, **kwargs):
            return await wrapper_fn(original_fn.__get__(self, type(self)), self, args, kwargs, *wrapper_args)
    else:

        @functools.wraps(original_fn)
        def wrapper(self, *args, **kwargs):
            return wrapper_fn(original_fn.__get__(self, type(self)), self, args, kwargs, *wrapper_args)

    wrapper.__dd_wrapped__ = original_fn
    return wrapper


def _create_event(
    instance: Any,
    func: Callable[..., Any],
    request_kwargs: dict[str, Any],
    interface_type: str,
    model: str = "",
    is_chat: Optional[bool] = None,
    operation: str = "",
) -> LlmRequestEvent:
    return LlmRequestEvent(
        component="llama_index",
        service=int_service(None, _integration.integration_config),
        resource=f"{instance.__class__.__name__}.{func.__name__}",
        provider="llama_index",
        model=model,
        integration=_integration,
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
        interface_type=interface_type,
        instance=instance,
        is_chat=is_chat,
        operation=operation,
    )


def _run_llm_sync(
    func,
    instance,
    args,
    kwargs,
    request_kwargs,
    model,
    interface_type,
    is_chat,
    always_stream=False,
):
    with core.context_with_event(
        _create_event(instance, func, request_kwargs, interface_type, model=model, is_chat=is_chat)
    ) as ctx:
        resp = func(*args, **kwargs)
        if always_stream or is_generator(resp):
            ctx.event.is_stream = True
            return handle_streamed_response(_integration, resp, args, request_kwargs, ctx.span, is_chat=is_chat)
        ctx.set_item("response", resp)
        return resp


async def _run_llm_async(
    func,
    instance,
    args,
    kwargs,
    request_kwargs,
    model,
    interface_type,
    is_chat,
    always_stream=False,
):
    with core.context_with_event(
        _create_event(instance, func, request_kwargs, interface_type, model=model, is_chat=is_chat)
    ) as ctx:
        resp = await func(*args, **kwargs)
        if always_stream or is_async_generator(resp):
            ctx.event.is_stream = True
            return handle_streamed_response(_integration, resp, args, request_kwargs, ctx.span, is_chat=is_chat)
        ctx.set_item("response", resp)
        return resp


def _run_simple_sync(
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    request_kwargs_builder: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]],
    interface_type: str,
    operation: str,
):
    request_kwargs = request_kwargs_builder(args, kwargs)
    with core.context_with_event(_create_event(instance, func, request_kwargs, interface_type, operation=operation)) as ctx:
        resp = func(*args, **kwargs)
        ctx.set_item("response", resp)
        return resp


async def _run_simple_async(
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    request_kwargs_builder: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]],
    interface_type: str,
    operation: str,
):
    request_kwargs = request_kwargs_builder(args, kwargs)
    with core.context_with_event(_create_event(instance, func, request_kwargs, interface_type, operation=operation)) as ctx:
        resp = await func(*args, **kwargs)
        ctx.set_item("response", resp)
        return resp


def _chat_wrapper(func, instance, args, kwargs, always_stream=False):
    request_kwargs, model = build_chat_request_kwargs(instance, args, kwargs)
    return _run_llm_sync(
        func, instance, args, kwargs, request_kwargs, model, "chat_model", is_chat=True, always_stream=always_stream
    )


def _complete_wrapper(func, instance, args, kwargs, always_stream=False):
    request_kwargs, model = build_complete_request_kwargs(instance, args, kwargs)
    return _run_llm_sync(
        func, instance, args, kwargs, request_kwargs, model, "completion", is_chat=False, always_stream=always_stream
    )


def _predict_wrapper(func, instance, args, kwargs):
    request_kwargs, model = build_predict_request_kwargs(instance, args, kwargs)
    return _run_llm_sync(func, instance, args, kwargs, request_kwargs, model, "completion", is_chat=False)


async def _achat_wrapper(func, instance, args, kwargs, always_stream=False):
    request_kwargs, model = build_chat_request_kwargs(instance, args, kwargs)
    return await _run_llm_async(
        func, instance, args, kwargs, request_kwargs, model, "chat_model", is_chat=True, always_stream=always_stream
    )


async def _acomplete_wrapper(func, instance, args, kwargs, always_stream=False):
    request_kwargs, model = build_complete_request_kwargs(instance, args, kwargs)
    return await _run_llm_async(
        func, instance, args, kwargs, request_kwargs, model, "completion", is_chat=False, always_stream=always_stream
    )


async def _apredict_wrapper(func, instance, args, kwargs):
    request_kwargs, model = build_predict_request_kwargs(instance, args, kwargs)
    return await _run_llm_async(func, instance, args, kwargs, request_kwargs, model, "completion", is_chat=False)


def _query_wrapper(func, instance, args, kwargs):
    return _run_simple_sync(func, instance, args, kwargs, build_query_request_kwargs, "query", "query")


async def _aquery_wrapper(func, instance, args, kwargs):
    return await _run_simple_async(func, instance, args, kwargs, build_query_request_kwargs, "query", "query")


def _retrieve_wrapper(func, instance, args, kwargs):
    return _run_simple_sync(func, instance, args, kwargs, build_query_request_kwargs, "retrieval", "retrieval")


async def _aretrieve_wrapper(func, instance, args, kwargs):
    return await _run_simple_async(
        func, instance, args, kwargs, build_query_request_kwargs, "retrieval", "retrieval"
    )


def _query_embedding_wrapper(func, instance, args, kwargs):
    return _run_simple_sync(
        func, instance, args, kwargs, build_query_embedding_request_kwargs, "embedding", "embedding"
    )


def _text_embedding_batch_wrapper(func, instance, args, kwargs):
    return _run_simple_sync(
        func, instance, args, kwargs, build_text_embedding_batch_request_kwargs, "embedding", "embedding"
    )


async def _aquery_embedding_wrapper(func, instance, args, kwargs):
    return await _run_simple_async(
        func, instance, args, kwargs, build_query_embedding_request_kwargs, "embedding", "embedding"
    )


async def _atext_embedding_batch_wrapper(func, instance, args, kwargs):
    return await _run_simple_async(
        func, instance, args, kwargs, build_text_embedding_batch_request_kwargs, "embedding", "embedding"
    )


def _agent_run_wrapper(func, instance, args, kwargs):
    return _run_simple_sync(func, instance, args, kwargs, build_agent_run_request_kwargs, "agent", "agent")


async def _agent_call_tool_wrapper(func, instance, args, kwargs):
    return await _run_simple_async(
        func, instance, args, kwargs, build_agent_call_tool_request_kwargs, "tool", "tool"
    )


_LLM_WRAPPERS = {
    "chat": _chat_wrapper,
    "complete": _complete_wrapper,
    "stream_chat": (_chat_wrapper, True),
    "stream_complete": (_complete_wrapper, True),
    "predict": _predict_wrapper,
    "achat": _achat_wrapper,
    "acomplete": _acomplete_wrapper,
    "astream_chat": (_achat_wrapper, True),
    "astream_complete": (_acomplete_wrapper, True),
    "apredict": _apredict_wrapper,
}

_QUERY_ENGINE_WRAPPERS = {
    "query": _query_wrapper,
    "aquery": _aquery_wrapper,
}

_RETRIEVER_WRAPPERS = {
    "retrieve": _retrieve_wrapper,
    "aretrieve": _aretrieve_wrapper,
}

_EMBEDDING_WRAPPERS = {
    "get_query_embedding": _query_embedding_wrapper,
    "get_text_embedding_batch": _text_embedding_batch_wrapper,
    "aget_query_embedding": _aquery_embedding_wrapper,
    "aget_text_embedding_batch": _atext_embedding_batch_wrapper,
}

_AGENT_WRAPPERS = {
    "run": _agent_run_wrapper,
    "call_tool": _agent_call_tool_wrapper,
}


def _wrap_class(cls, wrappers):
    """Wrap methods on *cls* using plain function replacement."""
    for method_name, wrapper in wrappers.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not hasattr(original, _DD_WRAPPED):
            try:
                wrapper_fn = wrapper
                wrapper_args = ()
                if isinstance(wrapper, tuple):
                    wrapper_fn = wrapper[0]
                    wrapper_args = wrapper[1:]
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_wrapper(wrapper_fn, original, wrapper_args))
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
