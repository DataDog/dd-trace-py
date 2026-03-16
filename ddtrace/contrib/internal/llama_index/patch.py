import functools
import inspect
import sys
from typing import Any
from typing import Callable

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.llama_index._streaming import handle_async_streamed_response
from ddtrace.contrib.internal.llama_index._streaming import handle_streamed_response
from ddtrace.contrib.internal.llama_index._utils import build_agent_run_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_agent_tool_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_chat_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_complete_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_embedding_batch_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_embedding_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_predict_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_query_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_retrieve_kwargs
from ddtrace.contrib.internal.llama_index._utils import get_model_name
from ddtrace.contrib.internal.llama_index._utils import is_async_generator
from ddtrace.contrib.internal.llama_index._utils import is_generator
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import LlamaIndexIntegration


log = get_logger(__name__)


def _get_llama_index_core():
    """Get the llama_index.core module, handling the case where it's being imported.

    AIDEV-NOTE: Cannot use `import llama_index.core` during the ddtrace-run import hook
    because Python's import machinery hasn't set the 'core' attribute on the 'llama_index'
    parent module yet, causing 'cannot access submodule' errors. Using sys.modules avoids this.
    """
    return sys.modules.get("llama_index.core") or __import__("llama_index.core", fromlist=["core"])


def get_version() -> str:
    core = _get_llama_index_core()
    return getattr(core, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"llama_index.core": ">=0.11.0"}


config._add("llama_index", {})


# AIDEV-NOTE: Track original methods per class so we can unwrap later.
# Maps (cls, method_name) -> original_method
_originals: dict[tuple[type, str], Any] = {}
_wrapped_classes: set[type] = set()

# Module-level reference to the integration instance, set during patch()
_integration = None


# ---------------------------------------------------------------------------
# Method wrapper factory
# ---------------------------------------------------------------------------

# AIDEV-NOTE: We use functools.wraps-based monkey-patching instead of wrapt's wrap()/unwrap()
# because LlamaIndex base classes (BaseLLM, BaseQueryEngine, etc.) inherit from Pydantic V2
# BaseModel. Pydantic V2 uses custom __get_pydantic_core_schema__ descriptors and metaclass
# machinery that conflicts with wrapt's BoundFunctionWrapper/ObjectProxy descriptors, causing
# TypeError during model instantiation or method dispatch. Plain function replacement via
# setattr + functools.wraps is transparent to Pydantic's descriptor protocol.

_DD_WRAPPED = "__dd_wrapped__"


def _make_wrapper(wrapper_fn, original_fn):
    """Create a sync or async method wrapper, auto-detected from *wrapper_fn*."""
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


# ---------------------------------------------------------------------------
# Event creation helper
# ---------------------------------------------------------------------------


def _create_llm_event(
    integration: LlamaIndexIntegration,
    instance: Any,
    func: Callable[..., Any],
    request_kwargs: dict[str, Any],
    interface_type: str,
    model: str = "",
    is_chat: bool | None = None,
    operation: str = "",
) -> LlmRequestEvent:
    """Create an LlmRequestEvent for a LlamaIndex call."""
    return LlmRequestEvent(
        component="llama_index",
        service=int_service(None, integration.integration_config),
        resource=f"{instance.__class__.__name__}.{func.__name__}",
        provider="llama_index",
        model=model,
        integration=integration,
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
        interface_type=interface_type,
        instance=instance,
        is_chat=is_chat,
        operation=operation,
    )


# ---------------------------------------------------------------------------
# Traced function factories
#
# AIDEV-NOTE: Two parallel factories (_traced_sync / _traced_async) that share
# identical structure. Uses core.context_with_event directly — typed fields on
# LlmRequestEvent (is_chat, operation, is_stream) replace ad-hoc ctx items.
# When is_chat is not None → LLM mode (model extraction, streaming support).
# When is_chat is None → operation mode (sets operation on event).
# ---------------------------------------------------------------------------


def _traced_sync(build_kw, *, is_chat=None, may_stream=False, always_stream=False, interface_type="", operation=""):
    """Factory for sync traced wrappers."""
    is_llm = is_chat is not None
    if is_llm:
        interface_type = "chat_model" if is_chat else "completion"

    def wrapper(func, instance, args, kwargs):
        model = get_model_name(instance) if is_llm else ""
        kw = build_kw(instance, args, kwargs)
        event = _create_llm_event(
            _integration, instance, func, kw, interface_type, model=model, is_chat=is_chat, operation=operation
        )

        with core.context_with_event(event) as ctx:
            resp = func(*args, **kwargs)
            if always_stream or (may_stream and is_generator(resp)):
                event.is_stream = True
                return handle_streamed_response(_integration, resp, args, kw, ctx.span, is_chat=is_chat)
            ctx.set_item("response", resp)
            return resp

    return wrapper


def _traced_async(build_kw, *, is_chat=None, may_stream=False, always_stream=False, interface_type="", operation=""):
    """Factory for async traced wrappers."""
    is_llm = is_chat is not None
    if is_llm:
        interface_type = "chat_model" if is_chat else "completion"

    async def wrapper(func, instance, args, kwargs):
        model = get_model_name(instance) if is_llm else ""
        kw = build_kw(instance, args, kwargs)
        event = _create_llm_event(
            _integration, instance, func, kw, interface_type, model=model, is_chat=is_chat, operation=operation
        )

        with core.context_with_event(event) as ctx:
            resp = await func(*args, **kwargs)
            if always_stream or (may_stream and is_async_generator(resp)):
                event.is_stream = True
                return handle_async_streamed_response(_integration, resp, args, kw, ctx.span, is_chat=is_chat)
            ctx.set_item("response", resp)
            return resp

    return wrapper


# ---------------------------------------------------------------------------
# AIDEV-NOTE: Method-to-wrapper mappings per component type.
# Each dict maps method_name -> wrapper_fn (sync and async combined).
# _make_wrapper auto-detects sync/async from the wrapper function.
# ---------------------------------------------------------------------------

_LLM_WRAPPERS = {
    "chat": _traced_sync(build_chat_kwargs, is_chat=True, may_stream=True),
    "complete": _traced_sync(build_complete_kwargs, is_chat=False, may_stream=True),
    "stream_chat": _traced_sync(build_chat_kwargs, is_chat=True, always_stream=True),
    "stream_complete": _traced_sync(build_complete_kwargs, is_chat=False, always_stream=True),
    "predict": _traced_sync(build_predict_kwargs, is_chat=False),
    "achat": _traced_async(build_chat_kwargs, is_chat=True, may_stream=True),
    "acomplete": _traced_async(build_complete_kwargs, is_chat=False, may_stream=True),
    "astream_chat": _traced_async(build_chat_kwargs, is_chat=True, always_stream=True),
    "astream_complete": _traced_async(build_complete_kwargs, is_chat=False, always_stream=True),
    "apredict": _traced_async(build_predict_kwargs, is_chat=False),
}

_QUERY_ENGINE_WRAPPERS = {
    "query": _traced_sync(build_query_kwargs, interface_type="query", operation="query"),
    "aquery": _traced_async(build_query_kwargs, interface_type="query", operation="query"),
}

_RETRIEVER_WRAPPERS = {
    "retrieve": _traced_sync(build_retrieve_kwargs, interface_type="retrieval", operation="retrieval"),
    "aretrieve": _traced_async(build_retrieve_kwargs, interface_type="retrieval", operation="retrieval"),
}

_EMBEDDING_WRAPPERS = {
    "get_query_embedding": _traced_sync(build_embedding_kwargs, interface_type="embedding", operation="embedding"),
    "get_text_embedding_batch": _traced_sync(
        build_embedding_batch_kwargs, interface_type="embedding", operation="embedding"
    ),
    "aget_query_embedding": _traced_async(build_embedding_kwargs, interface_type="embedding", operation="embedding"),
    "aget_text_embedding_batch": _traced_async(
        build_embedding_batch_kwargs, interface_type="embedding", operation="embedding"
    ),
}

_AGENT_WRAPPERS = {
    "run": _traced_sync(build_agent_run_kwargs, interface_type="agent", operation="agent"),
    "call_tool": _traced_async(build_agent_tool_kwargs, interface_type="tool", operation="tool"),
}

_ALL_WRAPPERS = [_LLM_WRAPPERS, _QUERY_ENGINE_WRAPPERS, _RETRIEVER_WRAPPERS, _EMBEDDING_WRAPPERS, _AGENT_WRAPPERS]


# ---------------------------------------------------------------------------
# Generic wrap / unwrap
# ---------------------------------------------------------------------------


def _is_dd_wrapped(method):
    return hasattr(method, _DD_WRAPPED)


def _wrap_class(cls, wrappers):
    """Wrap methods on a class using plain function replacement."""
    for method_name, wrapper_fn in wrappers.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)
    _wrapped_classes.add(cls)


def _unwrap_class(cls, wrappers):
    """Unwrap methods on a class by restoring originals."""
    for method_name in wrappers:
        key = (cls, method_name)
        if key in _originals:
            try:
                setattr(cls, method_name, _originals.pop(key))
            except Exception:
                log.debug("Failed to unwrap %s.%s", cls.__name__, method_name, exc_info=True)


def _get_all_subclasses(base_cls):
    result = set()
    for sub in base_cls.__subclasses__():
        result.add(sub)
        result.update(_get_all_subclasses(sub))
    return result


def _patched_init(original_init):
    """Create a wrapper for BaseLLM.__init__ that wraps new subclass methods on first instantiation."""

    @functools.wraps(original_init)
    def wrapper(self, *args, **kwargs):
        result = original_init(self, *args, **kwargs)
        cls = type(self)
        if cls not in _wrapped_classes:
            _wrap_class(cls, _LLM_WRAPPERS)
        return result

    wrapper.__dd_wrapped__ = original_init
    return wrapper


# ---------------------------------------------------------------------------
# patch / unpatch
# ---------------------------------------------------------------------------


def patch():
    global _integration

    core_mod = _get_llama_index_core()
    BaseLLM = core_mod.base.llms.base.BaseLLM
    BaseQueryEngine = core_mod.base.base_query_engine.BaseQueryEngine
    BaseRetriever = core_mod.base.base_retriever.BaseRetriever
    BaseEmbedding = core_mod.base.embeddings.base.BaseEmbedding

    if getattr(core_mod, "_datadog_patch", False):
        return

    core_mod._datadog_patch = True

    integration = LlamaIndexIntegration(integration_config=config.llama_index)
    core_mod._datadog_integration = integration
    _integration = integration

    # AIDEV-NOTE: Hook __init__ on BaseLLM to wrap subclass methods on first instantiation.
    # This catches subclasses that were defined before patch() was called (e.g. OpenAI).
    original_init = BaseLLM.__init__
    _originals[(BaseLLM, "__init__")] = original_init
    BaseLLM.__init__ = _patched_init(original_init)

    # Wrap base classes and all existing subclasses
    for base_cls, wrappers in [
        (BaseLLM, _LLM_WRAPPERS),
        (BaseQueryEngine, _QUERY_ENGINE_WRAPPERS),
        (BaseRetriever, _RETRIEVER_WRAPPERS),
        (BaseEmbedding, _EMBEDDING_WRAPPERS),
    ]:
        for cls in [base_cls] + list(_get_all_subclasses(base_cls)):
            _wrap_class(cls, wrappers)

    # AIDEV-NOTE: Wrap BaseWorkflowAgent.run()/call_tool() — AI agent execution.
    # This module may not exist in older llama-index-core versions (e.g. ~=0.11.0).
    try:
        agent_mod = sys.modules.get("llama_index.core.agent.workflow.base_agent") or __import__(
            "llama_index.core.agent.workflow.base_agent", fromlist=["base_agent"]
        )
        BaseWorkflowAgent = agent_mod.BaseWorkflowAgent
        for cls in [BaseWorkflowAgent] + list(_get_all_subclasses(BaseWorkflowAgent)):
            _wrap_class(cls, _AGENT_WRAPPERS)
    except (ImportError, ModuleNotFoundError, AttributeError):
        log.debug("llama_index.core.agent.workflow not available, skipping agent instrumentation")


def unpatch():
    global _integration

    core_mod = _get_llama_index_core()

    if not getattr(core_mod, "_datadog_patch", False):
        return

    core_mod._datadog_patch = False

    init_key = (core_mod.base.llms.base.BaseLLM, "__init__")
    if init_key in _originals:
        core_mod.base.llms.base.BaseLLM.__init__ = _originals.pop(init_key)

    # Unwrap all wrapped classes (base classes + subclasses)
    for cls in list(_wrapped_classes):
        for wrappers in _ALL_WRAPPERS:
            _unwrap_class(cls, wrappers)
    _wrapped_classes.clear()
    _originals.clear()

    if hasattr(core_mod, "_datadog_integration"):
        delattr(core_mod, "_datadog_integration")
    _integration = None
