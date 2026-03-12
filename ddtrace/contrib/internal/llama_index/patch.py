import functools
import sys
from typing import Any

from ddtrace import config
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
# Method wrapper factories
# ---------------------------------------------------------------------------

# AIDEV-NOTE: We use functools.wraps-based monkey-patching instead of wrapt's wrap()/unwrap()
# because LlamaIndex base classes (BaseLLM, BaseQueryEngine, etc.) inherit from Pydantic V2
# BaseModel. Pydantic V2 uses custom __get_pydantic_core_schema__ descriptors and metaclass
# machinery that conflicts with wrapt's BoundFunctionWrapper/ObjectProxy descriptors, causing
# TypeError during model instantiation or method dispatch. Plain function replacement via
# setattr + functools.wraps is transparent to Pydantic's descriptor protocol.

_DD_WRAPPED = "__dd_wrapped__"


def _make_sync_wrapper(wrapper_fn, original_fn):
    @functools.wraps(original_fn)
    def wrapper(self, *args, **kwargs):
        return wrapper_fn(original_fn.__get__(self, type(self)), self, args, kwargs)

    wrapper.__dd_wrapped__ = original_fn
    return wrapper


def _make_async_wrapper(wrapper_fn, original_fn):
    @functools.wraps(original_fn)
    async def wrapper(self, *args, **kwargs):
        return await wrapper_fn(original_fn.__get__(self, type(self)), self, args, kwargs)

    wrapper.__dd_wrapped__ = original_fn
    return wrapper


# ---------------------------------------------------------------------------
# Traced function factories
#
# All build_kwargs_fn callables share the same signature:
#   (instance, args, kwargs) -> dict
# ---------------------------------------------------------------------------


def _traced_llm(build_kwargs_fn, *, is_chat, may_stream=False, always_stream=False):
    """Factory for sync LLM traced wrappers.

    Handles three modes:
    - Simple (default): try/except/finally with llmobs_set_tags in finally
    - may_stream: check if response is a generator; delegate to streaming handler if so
    - always_stream: always delegate to streaming handler; tags set in except on error
    """
    interface_type = "chat_model" if is_chat else "completion"

    def traced(func, instance, args, kwargs):
        model = get_model_name(instance)
        span = _integration.trace(
            "%s.%s" % (instance.__class__.__name__, func.__name__),
            submit_to_llmobs=True,
            interface_type=interface_type,
            provider="llama_index",
            model=model,
            instance=instance,
        )
        span._set_ctx_item("_dd_is_chat", is_chat)

        if always_stream:
            try:
                response = func(*args, **kwargs)
                kw = build_kwargs_fn(instance, args, kwargs)
                return handle_streamed_response(_integration, response, args, kw, span, is_chat=is_chat)
            except Exception:
                span.set_exc_info(*sys.exc_info())
                _integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None)
                span.finish()
                raise

        response = None
        stream = False
        try:
            response = func(*args, **kwargs)
            if may_stream and is_generator(response):
                stream = True
                kw = build_kwargs_fn(instance, args, kwargs)
                return handle_streamed_response(_integration, response, args, kw, span, is_chat=is_chat)
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            if span.error or not stream:
                kw = build_kwargs_fn(instance, args, kwargs)
                _integration.llmobs_set_tags(span, args=args, kwargs=kw, response=response)
                span.finish()
        return response

    return traced


def _traced_llm_async(build_kwargs_fn, *, is_chat, may_stream=False, always_stream=False):
    """Factory for async LLM traced wrappers. See _traced_llm for mode descriptions."""
    interface_type = "chat_model" if is_chat else "completion"

    async def traced(func, instance, args, kwargs):
        model = get_model_name(instance)
        span = _integration.trace(
            "%s.%s" % (instance.__class__.__name__, func.__name__),
            submit_to_llmobs=True,
            interface_type=interface_type,
            provider="llama_index",
            model=model,
            instance=instance,
        )
        span._set_ctx_item("_dd_is_chat", is_chat)

        if always_stream:
            try:
                response = await func(*args, **kwargs)
                kw = build_kwargs_fn(instance, args, kwargs)
                return handle_async_streamed_response(_integration, response, args, kw, span, is_chat=is_chat)
            except Exception:
                span.set_exc_info(*sys.exc_info())
                _integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None)
                span.finish()
                raise

        response = None
        stream = False
        try:
            response = await func(*args, **kwargs)
            if may_stream and is_async_generator(response):
                stream = True
                kw = build_kwargs_fn(instance, args, kwargs)
                return handle_async_streamed_response(_integration, response, args, kw, span, is_chat=is_chat)
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            if span.error or not stream:
                kw = build_kwargs_fn(instance, args, kwargs)
                _integration.llmobs_set_tags(span, args=args, kwargs=kw, response=response)
                span.finish()
        return response

    return traced


def _traced_operation(interface_type, build_kwargs_fn, operation):
    """Factory for sync non-LLM traced wrappers (query, retrieve, embed, agent)."""

    def traced(func, instance, args, kwargs):
        span = _integration.trace(
            "%s.%s" % (instance.__class__.__name__, func.__name__),
            submit_to_llmobs=True,
            interface_type=interface_type,
            provider="llama_index",
        )
        response = None
        try:
            response = func(*args, **kwargs)
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            kw = build_kwargs_fn(instance, args, kwargs)
            _integration.llmobs_set_tags(span, args=args, kwargs=kw, response=response, operation=operation)
            span.finish()
        return response

    return traced


def _traced_operation_async(interface_type, build_kwargs_fn, operation):
    """Factory for async non-LLM traced wrappers."""

    async def traced(func, instance, args, kwargs):
        span = _integration.trace(
            "%s.%s" % (instance.__class__.__name__, func.__name__),
            submit_to_llmobs=True,
            interface_type=interface_type,
            provider="llama_index",
        )
        response = None
        try:
            response = await func(*args, **kwargs)
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            kw = build_kwargs_fn(instance, args, kwargs)
            _integration.llmobs_set_tags(span, args=args, kwargs=kw, response=response, operation=operation)
            span.finish()
        return response

    return traced


# ---------------------------------------------------------------------------
# AIDEV-NOTE: Method-to-wrapper mappings per component type.
# Each tuple is (sync_wrappers, async_wrappers).
# ---------------------------------------------------------------------------

_LLM_WRAPPERS = (
    {
        "chat": _traced_llm(build_chat_kwargs, is_chat=True, may_stream=True),
        "complete": _traced_llm(build_complete_kwargs, is_chat=False, may_stream=True),
        "stream_chat": _traced_llm(build_chat_kwargs, is_chat=True, always_stream=True),
        "stream_complete": _traced_llm(build_complete_kwargs, is_chat=False, always_stream=True),
        "predict": _traced_llm(build_predict_kwargs, is_chat=False),
    },
    {
        "achat": _traced_llm_async(build_chat_kwargs, is_chat=True, may_stream=True),
        "acomplete": _traced_llm_async(build_complete_kwargs, is_chat=False, may_stream=True),
        "astream_chat": _traced_llm_async(build_chat_kwargs, is_chat=True, always_stream=True),
        "astream_complete": _traced_llm_async(build_complete_kwargs, is_chat=False, always_stream=True),
        "apredict": _traced_llm_async(build_predict_kwargs, is_chat=False),
    },
)

_QUERY_ENGINE_WRAPPERS = (
    {"query": _traced_operation("query", build_query_kwargs, "query")},
    {"aquery": _traced_operation_async("query", build_query_kwargs, "query")},
)

_RETRIEVER_WRAPPERS = (
    {"retrieve": _traced_operation("retrieval", build_retrieve_kwargs, "retrieval")},
    {"aretrieve": _traced_operation_async("retrieval", build_retrieve_kwargs, "retrieval")},
)

_EMBEDDING_WRAPPERS = (
    {
        "get_query_embedding": _traced_operation("embedding", build_embedding_kwargs, "embedding"),
        "get_text_embedding_batch": _traced_operation("embedding", build_embedding_batch_kwargs, "embedding"),
    },
    {
        "aget_query_embedding": _traced_operation_async("embedding", build_embedding_kwargs, "embedding"),
        "aget_text_embedding_batch": _traced_operation_async("embedding", build_embedding_batch_kwargs, "embedding"),
    },
)

_AGENT_WRAPPERS = (
    {"run": _traced_operation("agent", build_agent_run_kwargs, "agent")},
    {"call_tool": _traced_operation_async("tool", build_agent_tool_kwargs, "tool")},
)

# All wrapper configs for bulk unwrap during unpatch
_ALL_WRAPPERS = [_LLM_WRAPPERS, _QUERY_ENGINE_WRAPPERS, _RETRIEVER_WRAPPERS, _EMBEDDING_WRAPPERS, _AGENT_WRAPPERS]


# ---------------------------------------------------------------------------
# Generic wrap / unwrap
# ---------------------------------------------------------------------------


def _is_dd_wrapped(method):
    return hasattr(method, _DD_WRAPPED)


def _wrap_class(cls, sync_wrappers, async_wrappers):
    """Wrap methods on a class using plain function replacement."""
    for method_name, wrapper_fn in sync_wrappers.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_sync_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    for method_name, wrapper_fn in async_wrappers.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_async_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    _wrapped_classes.add(cls)


def _unwrap_class(cls, sync_wrappers, async_wrappers):
    """Unwrap methods on a class by restoring originals."""
    for method_name in list(sync_wrappers) + list(async_wrappers):
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
            _wrap_class(cls, *_LLM_WRAPPERS)
        return result

    wrapper.__dd_wrapped__ = original_init
    return wrapper


# ---------------------------------------------------------------------------
# patch / unpatch
# ---------------------------------------------------------------------------


def patch():
    global _integration

    core = _get_llama_index_core()
    BaseLLM = core.base.llms.base.BaseLLM
    BaseQueryEngine = core.base.base_query_engine.BaseQueryEngine
    BaseRetriever = core.base.base_retriever.BaseRetriever
    BaseEmbedding = core.base.embeddings.base.BaseEmbedding

    if getattr(core, "_datadog_patch", False):
        return

    core._datadog_patch = True

    integration = LlamaIndexIntegration(integration_config=config.llama_index)
    core._datadog_integration = integration
    _integration = integration

    # AIDEV-NOTE: Hook __init__ on BaseLLM to wrap subclass methods on first instantiation.
    # This catches subclasses that were defined before patch() was called (e.g. OpenAI).
    original_init = BaseLLM.__init__
    _originals[(BaseLLM, "__init__")] = original_init
    BaseLLM.__init__ = _patched_init(original_init)

    # Wrap base classes and all existing subclasses
    for cls in [BaseLLM] + list(_get_all_subclasses(BaseLLM)):
        _wrap_class(cls, *_LLM_WRAPPERS)

    for cls in [BaseQueryEngine] + list(_get_all_subclasses(BaseQueryEngine)):
        _wrap_class(cls, *_QUERY_ENGINE_WRAPPERS)

    for cls in [BaseRetriever] + list(_get_all_subclasses(BaseRetriever)):
        _wrap_class(cls, *_RETRIEVER_WRAPPERS)

    for cls in [BaseEmbedding] + list(_get_all_subclasses(BaseEmbedding)):
        _wrap_class(cls, *_EMBEDDING_WRAPPERS)

    # AIDEV-NOTE: Wrap BaseWorkflowAgent.run()/call_tool() — AI agent execution.
    # This module may not exist in older llama-index-core versions (e.g. ~=0.11.0).
    try:
        agent_mod = sys.modules.get("llama_index.core.agent.workflow.base_agent") or __import__(
            "llama_index.core.agent.workflow.base_agent", fromlist=["base_agent"]
        )
        BaseWorkflowAgent = agent_mod.BaseWorkflowAgent
        for cls in [BaseWorkflowAgent] + list(_get_all_subclasses(BaseWorkflowAgent)):
            _wrap_class(cls, *_AGENT_WRAPPERS)
    except (ImportError, ModuleNotFoundError, AttributeError):
        log.debug("llama_index.core.agent.workflow not available, skipping agent instrumentation")


def unpatch():
    global _integration

    core = _get_llama_index_core()
    BaseLLM = core.base.llms.base.BaseLLM
    BaseQueryEngine = core.base.base_query_engine.BaseQueryEngine
    BaseRetriever = core.base.base_retriever.BaseRetriever
    BaseEmbedding = core.base.embeddings.base.BaseEmbedding

    if not getattr(core, "_datadog_patch", False):
        return

    core._datadog_patch = False

    init_key = (BaseLLM, "__init__")
    if init_key in _originals:
        BaseLLM.__init__ = _originals.pop(init_key)

    # Unwrap base classes
    _unwrap_class(BaseLLM, *_LLM_WRAPPERS)
    _unwrap_class(BaseQueryEngine, *_QUERY_ENGINE_WRAPPERS)
    _unwrap_class(BaseRetriever, *_RETRIEVER_WRAPPERS)
    _unwrap_class(BaseEmbedding, *_EMBEDDING_WRAPPERS)

    try:
        agent_mod = sys.modules.get("llama_index.core.agent.workflow.base_agent") or __import__(
            "llama_index.core.agent.workflow.base_agent", fromlist=["base_agent"]
        )
        BaseWorkflowAgent = agent_mod.BaseWorkflowAgent
        _unwrap_class(BaseWorkflowAgent, *_AGENT_WRAPPERS)
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass

    # Unwrap all wrapped subclasses
    for cls in list(_wrapped_classes):
        for sync_w, async_w in _ALL_WRAPPERS:
            _unwrap_class(cls, sync_w, async_w)
    _wrapped_classes.clear()
    _originals.clear()

    if hasattr(core, "_datadog_integration"):
        delattr(core, "_datadog_integration")
    _integration = None
