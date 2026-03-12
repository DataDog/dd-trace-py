import asyncio
import functools
import sys
from typing import Any
from typing import Generator

from ddtrace import config
from ddtrace.contrib.internal.llama_index._streaming import handle_async_streamed_response
from ddtrace.contrib.internal.llama_index._streaming import handle_streamed_response
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

# Sentinel to detect our wrappers
_DD_WRAPPED = "__dd_wrapped__"


def _get_model_name(instance: Any) -> str:
    for attr in ("model", "model_name"):
        val = getattr(instance, attr, None)
        if val:
            return str(val)
    metadata = getattr(instance, "metadata", None)
    if metadata:
        model_name = getattr(metadata, "model_name", None)
        if model_name:
            return str(model_name)
    return ""


def _is_generator(obj: Any) -> bool:
    return isinstance(obj, Generator)


def _is_async_generator(obj: Any) -> bool:
    return hasattr(obj, "__aiter__") and hasattr(obj, "__anext__") and not asyncio.isfuture(obj)


def _make_sync_wrapper(wrapper_fn, original_fn):
    # AIDEV-NOTE: We use functools.wraps-based monkey-patching instead of wrapt's wrap()/unwrap()
    # because LlamaIndex base classes (BaseLLM, BaseQueryEngine, etc.) inherit from Pydantic V2
    # BaseModel. Pydantic V2 uses custom __get_pydantic_core_schema__ descriptors and metaclass
    # machinery that conflicts with wrapt's BoundFunctionWrapper/ObjectProxy descriptors, causing
    # TypeError during model instantiation or method dispatch. Plain function replacement via
    # setattr + functools.wraps is transparent to Pydantic's descriptor protocol.

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


def traced_llm_chat(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", True)

    response = None
    stream = False
    try:
        response = func(*args, **kwargs)

        if _is_generator(response):
            stream = True
            stream_kwargs = _build_chat_kwargs(instance, args, kwargs)
            return handle_streamed_response(integration, response, args, stream_kwargs, span, is_chat=True)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error or not stream:
            trace_kwargs = _build_chat_kwargs(instance, args, kwargs)
            integration.llmobs_set_tags(span, args=args, kwargs=trace_kwargs, response=response)
            span.finish()
    return response


async def traced_llm_achat(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", True)

    response = None
    stream = False
    try:
        response = await func(*args, **kwargs)

        if _is_async_generator(response):
            stream = True
            stream_kwargs = _build_chat_kwargs(instance, args, kwargs)
            return handle_async_streamed_response(integration, response, args, stream_kwargs, span, is_chat=True)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error or not stream:
            trace_kwargs = _build_chat_kwargs(instance, args, kwargs)
            integration.llmobs_set_tags(span, args=args, kwargs=trace_kwargs, response=response)
            span.finish()
    return response


def traced_llm_complete(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="completion",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", False)

    response = None
    stream = False
    try:
        response = func(*args, **kwargs)

        if _is_generator(response):
            stream = True
            complete_kwargs = _build_complete_kwargs(instance, args, kwargs)
            return handle_streamed_response(integration, response, args, complete_kwargs, span, is_chat=False)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error or not stream:
            complete_kwargs = _build_complete_kwargs(instance, args, kwargs)
            integration.llmobs_set_tags(span, args=args, kwargs=complete_kwargs, response=response)
            span.finish()
    return response


async def traced_llm_acomplete(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="completion",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", False)

    response = None
    stream = False
    try:
        response = await func(*args, **kwargs)

        if _is_async_generator(response):
            stream = True
            complete_kwargs = _build_complete_kwargs(instance, args, kwargs)
            return handle_async_streamed_response(integration, response, args, complete_kwargs, span, is_chat=False)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error or not stream:
            complete_kwargs = _build_complete_kwargs(instance, args, kwargs)
            integration.llmobs_set_tags(span, args=args, kwargs=complete_kwargs, response=response)
            span.finish()
    return response


def traced_llm_stream_chat(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", True)

    try:
        response = func(*args, **kwargs)
        stream_kwargs = _build_chat_kwargs(instance, args, kwargs)
        return handle_streamed_response(integration, response, args, stream_kwargs, span, is_chat=True)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None)
        span.finish()
        raise


async def traced_llm_astream_chat(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", True)

    try:
        response = await func(*args, **kwargs)
        stream_kwargs = _build_chat_kwargs(instance, args, kwargs)
        return handle_async_streamed_response(integration, response, args, stream_kwargs, span, is_chat=True)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None)
        span.finish()
        raise


def traced_llm_stream_complete(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="completion",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", False)

    try:
        response = func(*args, **kwargs)
        complete_kwargs = _build_complete_kwargs(instance, args, kwargs)
        return handle_streamed_response(integration, response, args, complete_kwargs, span, is_chat=False)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None)
        span.finish()
        raise


async def traced_llm_astream_complete(func, instance, args, kwargs):
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="completion",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", False)

    try:
        response = await func(*args, **kwargs)
        complete_kwargs = _build_complete_kwargs(instance, args, kwargs)
        return handle_async_streamed_response(integration, response, args, complete_kwargs, span, is_chat=False)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None)
        span.finish()
        raise


def traced_llm_predict(func, instance, args, kwargs):
    """Trace LLM.predict() — framework-level LLM invocation that routes to chat/complete."""
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="completion",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", False)

    response = None
    try:
        response = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        predict_kwargs = _build_predict_kwargs(instance, args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=predict_kwargs, response=response)
        span.finish()
    return response


async def traced_llm_apredict(func, instance, args, kwargs):
    """Trace async LLM.apredict() — async framework-level LLM invocation."""
    integration = _integration
    model = _get_model_name(instance)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="completion",
        provider="llama_index",
        model=model,
        instance=instance,
    )
    span._set_ctx_item("_dd_is_chat", False)

    response = None
    try:
        response = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        predict_kwargs = _build_predict_kwargs(instance, args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=predict_kwargs, response=response)
        span.finish()
    return response


def _build_predict_kwargs(instance, args, kwargs):
    """Build kwargs dict for predict() calls. The first arg is a PromptTemplate; format it as the prompt string."""
    trace_kwargs = dict(kwargs)
    if args:
        prompt_template = args[0]
        # PromptTemplate has a .format() method, but we just capture the template text
        template = getattr(prompt_template, "template", None)
        trace_kwargs["prompt"] = str(template) if template else str(prompt_template)
    trace_kwargs["model"] = _get_model_name(instance)
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        trace_kwargs["max_tokens"] = max_tokens
    return trace_kwargs


def traced_query_engine_query(func, instance, args, kwargs):
    """Trace BaseQueryEngine.query() calls — the primary RAG pipeline entry point."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="query",
        provider="llama_index",
    )

    response = None
    try:
        response = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        query_kwargs = _build_query_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=query_kwargs, response=response, operation="query")
        span.finish()
    return response


async def traced_query_engine_aquery(func, instance, args, kwargs):
    """Trace async BaseQueryEngine.aquery() calls."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="query",
        provider="llama_index",
    )

    response = None
    try:
        response = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        query_kwargs = _build_query_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=query_kwargs, response=response, operation="query")
        span.finish()
    return response


def traced_retriever_retrieve(func, instance, args, kwargs):
    """Trace BaseRetriever.retrieve() calls — core vector similarity search."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="retrieval",
        provider="llama_index",
    )

    response = None
    try:
        response = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        retrieve_kwargs = _build_retrieve_kwargs(args, kwargs)
        integration.llmobs_set_tags(
            span, args=args, kwargs=retrieve_kwargs, response=response, operation="retrieval"
        )
        span.finish()
    return response


async def traced_retriever_aretrieve(func, instance, args, kwargs):
    """Trace async BaseRetriever.aretrieve() calls."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="retrieval",
        provider="llama_index",
    )

    response = None
    try:
        response = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        retrieve_kwargs = _build_retrieve_kwargs(args, kwargs)
        integration.llmobs_set_tags(
            span, args=args, kwargs=retrieve_kwargs, response=response, operation="retrieval"
        )
        span.finish()
    return response


def traced_embedding_get_query(func, instance, args, kwargs):
    """Trace BaseEmbedding.get_query_embedding() calls."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider="llama_index",
    )

    response = None
    try:
        response = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        embed_kwargs = _build_embedding_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=embed_kwargs, response=response, operation="embedding")
        span.finish()
    return response


async def traced_embedding_aget_query(func, instance, args, kwargs):
    """Trace async BaseEmbedding.aget_query_embedding() calls."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider="llama_index",
    )

    response = None
    try:
        response = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        embed_kwargs = _build_embedding_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=embed_kwargs, response=response, operation="embedding")
        span.finish()
    return response


def traced_embedding_get_text_batch(func, instance, args, kwargs):
    """Trace BaseEmbedding.get_text_embedding_batch() calls — batch document embedding for indexing."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider="llama_index",
    )

    response = None
    try:
        response = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        embed_kwargs = _build_embedding_batch_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=embed_kwargs, response=response, operation="embedding")
        span.finish()
    return response


async def traced_embedding_aget_text_batch(func, instance, args, kwargs):
    """Trace async BaseEmbedding.aget_text_embedding_batch() calls."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider="llama_index",
    )

    response = None
    try:
        response = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        embed_kwargs = _build_embedding_batch_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=embed_kwargs, response=response, operation="embedding")
        span.finish()
    return response


def _build_embedding_kwargs(args, kwargs):
    """Build kwargs dict with the query text for embedding calls."""
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["query"] = str(args[0])
    elif "query" in kwargs:
        trace_kwargs["query"] = str(kwargs["query"])
    return trace_kwargs


def _build_embedding_batch_kwargs(args, kwargs):
    """Build kwargs dict with the texts for batch embedding calls."""
    trace_kwargs = dict(kwargs)
    if args:
        texts = args[0]
        trace_kwargs["query"] = "[%d texts]" % len(texts) if texts else ""
    elif "texts" in kwargs:
        texts = kwargs["texts"]
        trace_kwargs["query"] = "[%d texts]" % len(texts) if texts else ""
    return trace_kwargs


def traced_agent_run(func, instance, args, kwargs):
    """Trace BaseWorkflowAgent.run() — AI agent execution loop entry point."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="agent",
        provider="llama_index",
    )

    response = None
    try:
        response = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        agent_kwargs = _build_agent_run_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=agent_kwargs, response=response, operation="agent")
        span.finish()
    return response


async def traced_agent_call_tool(func, instance, args, kwargs):
    """Trace async BaseWorkflowAgent.call_tool() — individual tool execution within agent loop."""
    integration = _integration

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="tool",
        provider="llama_index",
    )

    response = None
    try:
        response = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        tool_kwargs = _build_agent_tool_kwargs(args, kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=tool_kwargs, response=response, operation="tool")
        span.finish()
    return response


def _build_agent_run_kwargs(args, kwargs):
    """Build kwargs dict for agent run() calls."""
    trace_kwargs = dict(kwargs)
    if args:
        user_msg = args[0]
        if user_msg is not None:
            content = getattr(user_msg, "content", None)
            trace_kwargs["input"] = str(content) if content else str(user_msg)
    elif "user_msg" in kwargs and kwargs["user_msg"] is not None:
        user_msg = kwargs["user_msg"]
        content = getattr(user_msg, "content", None)
        trace_kwargs["input"] = str(content) if content else str(user_msg)
    return trace_kwargs


def _build_agent_tool_kwargs(args, kwargs):
    """Build kwargs dict for agent call_tool() calls."""
    trace_kwargs = dict(kwargs)
    # call_tool receives (ctx, ev) where ev is a ToolCall event
    if len(args) >= 2:
        tool_call_ev = args[1]
        tool_name = getattr(tool_call_ev, "tool_name", None)
        if tool_name:
            trace_kwargs["tool_name"] = str(tool_name)
    elif "ev" in kwargs:
        tool_call_ev = kwargs["ev"]
        tool_name = getattr(tool_call_ev, "tool_name", None)
        if tool_name:
            trace_kwargs["tool_name"] = str(tool_name)
    return trace_kwargs


def _build_retrieve_kwargs(args, kwargs):
    """Build kwargs dict with the query string for retriever calls."""
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["query_str"] = str(args[0])
    elif "str_or_query_bundle" in kwargs:
        query = kwargs["str_or_query_bundle"]
        trace_kwargs["query_str"] = getattr(query, "query_str", str(query))
    return trace_kwargs


def _build_query_kwargs(args, kwargs):
    """Build kwargs dict with the query string for the integration layer."""
    trace_kwargs = dict(kwargs)
    if args:
        query = args[0]
        # QueryBundle has a query_str attribute; plain strings are used directly
        trace_kwargs["query_str"] = getattr(query, "query_str", str(query))
    elif "str_or_query_bundle" in kwargs:
        query = kwargs["str_or_query_bundle"]
        trace_kwargs["query_str"] = getattr(query, "query_str", str(query))
    return trace_kwargs


def _build_chat_kwargs(instance, args, kwargs):
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["messages"] = args[0]
    elif "messages" in kwargs:
        trace_kwargs["messages"] = kwargs["messages"]
    trace_kwargs["model"] = _get_model_name(instance)
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        trace_kwargs["max_tokens"] = max_tokens
    return trace_kwargs


def _build_complete_kwargs(instance, args, kwargs):
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["prompt"] = args[0]
    elif "prompt" in kwargs:
        trace_kwargs["prompt"] = kwargs["prompt"]
    trace_kwargs["model"] = _get_model_name(instance)
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        trace_kwargs["max_tokens"] = max_tokens
    return trace_kwargs


# AIDEV-NOTE: Method name to wrapper_fn mapping for LLM methods.
_SYNC_METHOD_WRAPPERS = {
    "chat": traced_llm_chat,
    "complete": traced_llm_complete,
    "stream_chat": traced_llm_stream_chat,
    "stream_complete": traced_llm_stream_complete,
    "predict": traced_llm_predict,
}

_ASYNC_METHOD_WRAPPERS = {
    "achat": traced_llm_achat,
    "acomplete": traced_llm_acomplete,
    "astream_chat": traced_llm_astream_chat,
    "astream_complete": traced_llm_astream_complete,
    "apredict": traced_llm_apredict,
}

# AIDEV-NOTE: Method name to wrapper_fn mapping for QueryEngine methods.
_QUERY_ENGINE_SYNC_WRAPPERS = {
    "query": traced_query_engine_query,
}

_QUERY_ENGINE_ASYNC_WRAPPERS = {
    "aquery": traced_query_engine_aquery,
}

# AIDEV-NOTE: Method name to wrapper_fn mapping for Retriever methods.
_RETRIEVER_SYNC_WRAPPERS = {
    "retrieve": traced_retriever_retrieve,
}

_RETRIEVER_ASYNC_WRAPPERS = {
    "aretrieve": traced_retriever_aretrieve,
}

# AIDEV-NOTE: Method name to wrapper_fn mapping for Embedding methods.
_EMBEDDING_SYNC_WRAPPERS = {
    "get_query_embedding": traced_embedding_get_query,
    "get_text_embedding_batch": traced_embedding_get_text_batch,
}

_EMBEDDING_ASYNC_WRAPPERS = {
    "aget_query_embedding": traced_embedding_aget_query,
    "aget_text_embedding_batch": traced_embedding_aget_text_batch,
}

# AIDEV-NOTE: Method name to wrapper_fn mapping for Agent methods.
_AGENT_SYNC_WRAPPERS = {
    "run": traced_agent_run,
}

_AGENT_ASYNC_WRAPPERS = {
    "call_tool": traced_agent_call_tool,
}


def _is_dd_wrapped(method):
    return hasattr(method, _DD_WRAPPED)


def _wrap_llm_class(cls):
    """Wrap all LLM methods on a given class using plain function replacement."""
    for method_name, wrapper_fn in _SYNC_METHOD_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_sync_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    for method_name, wrapper_fn in _ASYNC_METHOD_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_async_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    _wrapped_classes.add(cls)


def _unwrap_llm_class(cls):
    all_methods = list(_SYNC_METHOD_WRAPPERS.keys()) + list(_ASYNC_METHOD_WRAPPERS.keys())
    for method_name in all_methods:
        key = (cls, method_name)
        if key in _originals:
            try:
                setattr(cls, method_name, _originals.pop(key))
            except Exception:
                log.debug("Failed to unwrap %s.%s", cls.__name__, method_name, exc_info=True)


def _wrap_query_engine_class(cls):
    """Wrap query/aquery methods on a QueryEngine class."""
    for method_name, wrapper_fn in _QUERY_ENGINE_SYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_sync_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    for method_name, wrapper_fn in _QUERY_ENGINE_ASYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_async_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    _wrapped_classes.add(cls)


def _unwrap_query_engine_class(cls):
    """Unwrap query/aquery methods on a QueryEngine class by restoring originals."""
    all_methods = list(_QUERY_ENGINE_SYNC_WRAPPERS.keys()) + list(_QUERY_ENGINE_ASYNC_WRAPPERS.keys())
    for method_name in all_methods:
        key = (cls, method_name)
        if key in _originals:
            try:
                setattr(cls, method_name, _originals.pop(key))
            except Exception:
                log.debug("Failed to unwrap %s.%s", cls.__name__, method_name, exc_info=True)


def _wrap_retriever_class(cls):
    """Wrap retrieve/aretrieve methods on a Retriever class."""
    for method_name, wrapper_fn in _RETRIEVER_SYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_sync_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    for method_name, wrapper_fn in _RETRIEVER_ASYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_async_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    _wrapped_classes.add(cls)


def _unwrap_retriever_class(cls):
    """Unwrap retrieve/aretrieve methods on a Retriever class by restoring originals."""
    all_methods = list(_RETRIEVER_SYNC_WRAPPERS.keys()) + list(_RETRIEVER_ASYNC_WRAPPERS.keys())
    for method_name in all_methods:
        key = (cls, method_name)
        if key in _originals:
            try:
                setattr(cls, method_name, _originals.pop(key))
            except Exception:
                log.debug("Failed to unwrap %s.%s", cls.__name__, method_name, exc_info=True)


def _wrap_embedding_class(cls):
    """Wrap embedding methods on a BaseEmbedding class."""
    for method_name, wrapper_fn in _EMBEDDING_SYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_sync_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    for method_name, wrapper_fn in _EMBEDDING_ASYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_async_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    _wrapped_classes.add(cls)


def _unwrap_embedding_class(cls):
    """Unwrap embedding methods on a BaseEmbedding class by restoring originals."""
    all_methods = list(_EMBEDDING_SYNC_WRAPPERS.keys()) + list(_EMBEDDING_ASYNC_WRAPPERS.keys())
    for method_name in all_methods:
        key = (cls, method_name)
        if key in _originals:
            try:
                setattr(cls, method_name, _originals.pop(key))
            except Exception:
                log.debug("Failed to unwrap %s.%s", cls.__name__, method_name, exc_info=True)


def _wrap_agent_class(cls):
    """Wrap run/call_tool methods on a BaseWorkflowAgent class."""
    for method_name, wrapper_fn in _AGENT_SYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_sync_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    for method_name, wrapper_fn in _AGENT_ASYNC_WRAPPERS.items():
        original = cls.__dict__.get(method_name)
        if original is not None and not _is_dd_wrapped(original):
            try:
                _originals[(cls, method_name)] = original
                setattr(cls, method_name, _make_async_wrapper(wrapper_fn, original))
            except Exception:
                log.debug("Failed to wrap %s.%s", cls.__name__, method_name, exc_info=True)

    _wrapped_classes.add(cls)


def _unwrap_agent_class(cls):
    """Unwrap run/call_tool methods on a BaseWorkflowAgent class by restoring originals."""
    all_methods = list(_AGENT_SYNC_WRAPPERS.keys()) + list(_AGENT_ASYNC_WRAPPERS.keys())
    for method_name in all_methods:
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
            _wrap_llm_class(cls)
        return result

    wrapper.__dd_wrapped__ = original_init
    return wrapper


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

    _wrap_llm_class(BaseLLM)
    for cls in _get_all_subclasses(BaseLLM):
        _wrap_llm_class(cls)

    # AIDEV-NOTE: Wrap BaseQueryEngine.query()/aquery() — the primary RAG pipeline entry points.
    _wrap_query_engine_class(BaseQueryEngine)
    for cls in _get_all_subclasses(BaseQueryEngine):
        _wrap_query_engine_class(cls)

    # AIDEV-NOTE: Wrap BaseRetriever.retrieve()/aretrieve() — core vector similarity search.
    _wrap_retriever_class(BaseRetriever)
    for cls in _get_all_subclasses(BaseRetriever):
        _wrap_retriever_class(cls)

    # AIDEV-NOTE: Wrap BaseEmbedding.get_query_embedding() — query embedding generation for RAG.
    _wrap_embedding_class(BaseEmbedding)
    for cls in _get_all_subclasses(BaseEmbedding):
        _wrap_embedding_class(cls)

    # AIDEV-NOTE: Wrap BaseWorkflowAgent.run()/call_tool() — AI agent execution.
    agent_mod = sys.modules.get("llama_index.core.agent.workflow.base_agent") or __import__(
        "llama_index.core.agent.workflow.base_agent", fromlist=["base_agent"]
    )
    BaseWorkflowAgent = agent_mod.BaseWorkflowAgent
    _wrap_agent_class(BaseWorkflowAgent)
    for cls in _get_all_subclasses(BaseWorkflowAgent):
        _wrap_agent_class(cls)


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

    _unwrap_llm_class(BaseLLM)
    _unwrap_query_engine_class(BaseQueryEngine)

    # Unwrap BaseRetriever and all wrapped subclasses
    _unwrap_retriever_class(BaseRetriever)

    # Unwrap BaseEmbedding and all wrapped subclasses
    _unwrap_embedding_class(BaseEmbedding)

    # Unwrap BaseWorkflowAgent and all wrapped subclasses
    agent_mod = sys.modules.get("llama_index.core.agent.workflow.base_agent") or __import__(
        "llama_index.core.agent.workflow.base_agent", fromlist=["base_agent"]
    )
    BaseWorkflowAgent = agent_mod.BaseWorkflowAgent
    _unwrap_agent_class(BaseWorkflowAgent)

    for cls in list(_wrapped_classes):
        _unwrap_llm_class(cls)
        _unwrap_query_engine_class(cls)
        _unwrap_retriever_class(cls)
        _unwrap_embedding_class(cls)
        _unwrap_agent_class(cls)
    _wrapped_classes.clear()
    _originals.clear()

    if hasattr(core, "_datadog_integration"):
        delattr(core, "_datadog_integration")
    _integration = None
