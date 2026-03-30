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
from ddtrace.contrib.internal.llama_index._utils import build_query_embedding_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_query_request_kwargs
from ddtrace.contrib.internal.llama_index._utils import build_text_embedding_batch_request_kwargs
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import LlamaIndexIntegration


log = get_logger(__name__)


def _llama_core():
    """Lazy accessor for ``llama_index.core``.

    Cannot be imported at module level because LlamaIndex classes inherit from
    Pydantic V2 ``BaseModel``, which triggers ``__init_subclass__`` at import
    time and interferes with early monkey-patching.
    """
    return sys.modules.get("llama_index.core") or __import__("llama_index.core", fromlist=["core"])


def get_version() -> str:
    return getattr(_llama_core(), "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"llama_index.core": ">=0.11.0"}


config._add("llama_index", {})

_originals: dict[tuple[type, str], Any] = {}
_wrapped_classes: set[type] = set()

# Stored as a module-level variable so wrapper closures (created at import time
# in the method-to-wrapper mappings below) can access the integration instance
# that is only available after patch() runs.
_integration = None

_DD_WRAPPED = "__dd_wrapped__"


# We use functools.wraps-based monkey-patching instead of wrapt because LlamaIndex
# classes inherit from Pydantic V2 BaseModel, which conflicts with wrapt's
# BoundFunctionWrapper descriptors (TypeError during model instantiation).


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
    model: Optional[str],
    operation: str,
) -> LlmRequestEvent:
    """Create an ``LlmRequestEvent`` for a LlamaIndex operation.

    For LLM calls (``operation=""``) the resource is the model name to keep
    cardinality low.  For non-LLM operations (query, retrieval, embedding,
    agent) the resource is the class name.
    """
    resource = model if (model and not operation) else instance.__class__.__name__
    return LlmRequestEvent(
        component="llama_index",
        service=int_service(None, _integration.integration_config),
        resource=resource,
        provider="llama_index",
        model=model,
        llmobs_integration=_integration,
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
        instance=instance,
        operation=operation,
    )


def _llm_wrapper(build_kwargs_fn, always_stream, operation=""):
    """Create a sync wrapper for an LLM or embedding method.

    ``build_kwargs_fn`` extracts request metadata from the call arguments.
    ``always_stream`` is True for methods like ``stream_chat`` whose return
    value is always a generator even though ``isinstance(..., Generator)``
    may not detect custom LlamaIndex stream wrappers.
    ``operation`` is non-empty for embedding calls to distinguish them from
    chat/complete LLM calls in LLMObs.
    """

    def wrapper(func, instance, args, kwargs):
        request_kwargs, model = build_kwargs_fn(instance, args, kwargs)
        event = _create_event(instance, func, request_kwargs, model=model, operation=operation)
        # dispatch_end_event=False: non-streaming calls dispatch manually after
        # capturing the response; streaming calls defer to finalize_stream().
        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                resp = func(*args, **kwargs)
            except Exception as e:
                ctx.dispatch_ended_event(type(e), e, e.__traceback__)
                raise
            if always_stream or isinstance(resp, Generator):
                return handle_streamed_response(_integration, resp, args, request_kwargs, ctx)
            event.response = resp
            ctx.dispatch_ended_event()
            return resp

    return wrapper


def _llm_wrapper_async(build_kwargs_fn, always_stream, operation=""):
    """Create an async wrapper for an LLM or embedding method.

    See ``_llm_wrapper`` for parameter descriptions.
    """

    async def wrapper(func, instance, args, kwargs):
        request_kwargs, model = build_kwargs_fn(instance, args, kwargs)
        event = _create_event(instance, func, request_kwargs, model=model, operation=operation)
        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                resp = await func(*args, **kwargs)
            except Exception as e:
                ctx.dispatch_ended_event(type(e), e, e.__traceback__)
                raise
            if always_stream or inspect.isasyncgen(resp):
                return handle_streamed_response(_integration, resp, args, request_kwargs, ctx)
            event.response = resp
            ctx.dispatch_ended_event()
            return resp

    return wrapper


def _operation_wrapper(build_kwargs_fn, operation):
    """Create a sync wrapper for a non-LLM operation (query, retrieve, agent).

    These never stream and have no model, so the wrapper is simpler.
    """

    def wrapper(func, instance, args, kwargs):
        request_kwargs = build_kwargs_fn(args, kwargs)
        event = _create_event(instance, func, request_kwargs, model=None, operation=operation)
        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                resp = func(*args, **kwargs)
            except Exception as e:
                ctx.dispatch_ended_event(type(e), e, e.__traceback__)
                raise
            event.response = resp
            ctx.dispatch_ended_event()
            return resp

    return wrapper


def _operation_wrapper_async(build_kwargs_fn, operation):
    """Create an async wrapper for a non-LLM operation (aquery, aretrieve, agent).

    See ``_operation_wrapper`` for parameter descriptions.
    """

    async def wrapper(func, instance, args, kwargs):
        request_kwargs = build_kwargs_fn(args, kwargs)
        event = _create_event(instance, func, request_kwargs, model=None, operation=operation)
        with core.context_with_event(event, dispatch_end_event=False) as ctx:
            try:
                resp = await func(*args, **kwargs)
            except Exception as e:
                ctx.dispatch_ended_event(type(e), e, e.__traceback__)
                raise
            event.response = resp
            ctx.dispatch_ended_event()
            return resp

    return wrapper


_LLM_WRAPPERS = {
    "chat": _llm_wrapper(build_chat_request_kwargs, always_stream=False),
    "complete": _llm_wrapper(build_complete_request_kwargs, always_stream=False),
    "stream_chat": _llm_wrapper(build_chat_request_kwargs, always_stream=True),
    "stream_complete": _llm_wrapper(build_complete_request_kwargs, always_stream=True),
    "achat": _llm_wrapper_async(build_chat_request_kwargs, always_stream=False),
    "acomplete": _llm_wrapper_async(build_complete_request_kwargs, always_stream=False),
    "astream_chat": _llm_wrapper_async(build_chat_request_kwargs, always_stream=True),
    "astream_complete": _llm_wrapper_async(build_complete_request_kwargs, always_stream=True),
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
    "get_query_embedding": _llm_wrapper(
        build_query_embedding_request_kwargs, always_stream=False, operation="embedding"
    ),
    "get_text_embedding_batch": _llm_wrapper(
        build_text_embedding_batch_request_kwargs, always_stream=False, operation="embedding"
    ),
    "aget_query_embedding": _llm_wrapper_async(
        build_query_embedding_request_kwargs, always_stream=False, operation="embedding"
    ),
    "aget_text_embedding_batch": _llm_wrapper_async(
        build_text_embedding_batch_request_kwargs, always_stream=False, operation="embedding"
    ),
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
    """Wrap ``BaseLLM.__init__`` to patch new subclass methods on first instantiation.

    LLM provider packages (e.g. ``llama_index.llms.openai``) may be imported
    after ``patch()`` runs, creating subclasses we haven't seen yet.  This hook
    ensures those late-arriving subclasses get their methods wrapped on their
    first instantiation.
    """

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

    # LlamaIndex LLM methods (chat, complete, etc.) are abstract on BaseLLM —
    # concrete subclasses override them entirely, so we must wrap each subclass.
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

    # Hook BaseLLM.__init__ so LLM subclasses imported after patch() get wrapped.
    # This handles the case where provider packages (llama_index.llms.openai, etc.)
    # are imported after ddtrace.auto has already called patch().
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
