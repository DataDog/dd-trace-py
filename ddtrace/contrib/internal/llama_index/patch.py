import sys
from typing import Dict

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import LlamaIndexIntegration


log = get_logger(__name__)


config._add("llama_index", {})

# LLM methods that are abstract on BaseLLM and must be wrapped on subclasses
_LLM_SYNC_METHODS = ("chat", "complete", "stream_chat", "stream_complete")
_LLM_ASYNC_METHODS = ("achat", "acomplete", "astream_chat", "astream_complete")

# Module-level reference set during patch(), used by __init__ hook at runtime
_llama_index_mod = None


def _supported_versions() -> Dict[str, str]:
    return {"llama_index": ">=0.1.0"}


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("llama-index-core")
    except Exception:
        return ""


@with_traced_module
def traced_llm_call(llama_index, pin, func, instance, args, kwargs):
    integration = llama_index._datadog_integration

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        model=kwargs.get("model", ""),
        instance=instance,
    )

    result = None
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error or result is not None:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result)
            span.finish()
    return result


@with_traced_module
async def traced_async_llm_call(llama_index, pin, func, instance, args, kwargs):
    integration = llama_index._datadog_integration

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        model=kwargs.get("model", ""),
        instance=instance,
    )

    result = None
    try:
        result = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if span.error or result is not None:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result)
            span.finish()
    return result


def _is_ddtrace_wrapped(cls, method_name):
    """Check if a method on cls has already been wrapped by ddtrace (not another library)."""
    method = cls.__dict__.get(method_name)
    return method is not None and getattr(method, "_datadog_patch", False) and hasattr(method, "__wrapped__")


def _patch_llm_subclass(cls):
    """Wrap LLM methods on a concrete subclass of BaseLLM.

    BaseLLM.chat/complete/etc. are abstract, so subclasses always override them.
    We must wrap each subclass's methods directly.
    Note: llama_index's own @dispatcher.span decorator uses wrapt FunctionWrapper,
    so iswrapped() returns True for those too. We use a custom marker to track our wrapping.
    """
    mod = _llama_index_mod
    if mod is None:
        return

    for method_name in _LLM_SYNC_METHODS:
        if method_name in cls.__dict__ and not _is_ddtrace_wrapped(cls, method_name):
            wrap(cls, method_name, traced_llm_call(mod))
            cls.__dict__[method_name]._datadog_patch = True

    for method_name in _LLM_ASYNC_METHODS:
        if method_name in cls.__dict__ and not _is_ddtrace_wrapped(cls, method_name):
            wrap(cls, method_name, traced_async_llm_call(mod))
            cls.__dict__[method_name]._datadog_patch = True


def _patch_all_llm_subclasses(cls):
    """Recursively wrap LLM methods on all existing subclasses."""
    for subcls in cls.__subclasses__():
        _patch_llm_subclass(subcls)
        _patch_all_llm_subclasses(subcls)


def _patched_llm_init(wrapped, instance, args, kwargs):
    """Hook into BaseLLM.__init__ to wrap methods on the concrete class."""
    wrapped(*args, **kwargs)
    cls = type(instance)
    _patch_llm_subclass(cls)


def patch():
    """Activate tracing for llama_index.

    Uses llama_index.core as the anchor module because llama_index itself
    is a namespace package and wrapt import hooks don't fire for namespace packages.
    """
    import llama_index.core as llama_index_core

    global _llama_index_mod

    if getattr(llama_index_core, "_datadog_patch", False):
        return

    llama_index_core._datadog_patch = True
    _llama_index_mod = llama_index_core

    Pin().onto(llama_index_core)
    integration = LlamaIndexIntegration(integration_config=config.llama_index)
    llama_index_core._datadog_integration = integration

    from llama_index.core.base.base_query_engine import BaseQueryEngine
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from llama_index.core.base.llms.base import BaseLLM

    wrap(BaseQueryEngine, "query", traced_llm_call(llama_index_core))
    wrap(BaseQueryEngine, "aquery", traced_async_llm_call(llama_index_core))

    wrap(BaseRetriever, "retrieve", traced_llm_call(llama_index_core))
    wrap(BaseRetriever, "aretrieve", traced_async_llm_call(llama_index_core))

    # LLM methods - abstract, so wrap via __init__ hook on subclasses
    wrap(BaseLLM, "__init__", _patched_llm_init)

    # Also wrap any existing subclasses that were created before patching
    _patch_all_llm_subclasses(BaseLLM)

    wrap(BaseEmbedding, "get_query_embedding", traced_llm_call(llama_index_core))
    wrap(BaseEmbedding, "aget_query_embedding", traced_async_llm_call(llama_index_core))
    wrap(BaseEmbedding, "get_text_embedding_batch", traced_llm_call(llama_index_core))
    wrap(BaseEmbedding, "aget_text_embedding_batch", traced_async_llm_call(llama_index_core))

    try:
        from llama_index.core.agent.workflow.multi_agent_workflow import AgentWorkflow
        from llama_index.core.agent.workflow.react_agent import ReActAgent

        wrap(ReActAgent, "take_step", traced_async_llm_call(llama_index_core))
        wrap(AgentWorkflow, "run_agent_step", traced_async_llm_call(llama_index_core))
    except ImportError:
        log.debug("llama_index agent workflow classes not available for patching")


def _unwrap_llm_subclasses(cls):
    for subcls in cls.__subclasses__():
        for method_name in _LLM_SYNC_METHODS + _LLM_ASYNC_METHODS:
            if _is_ddtrace_wrapped(subcls, method_name):
                try:
                    unwrap(subcls, method_name)
                except AttributeError:
                    # Method may have been partially unwrapped already
                    pass
                # Clean up the marker from the restored method (which may be
                # llama_index's own FunctionWrapper, not ours)
                method = subcls.__dict__.get(method_name)
                if method is not None and hasattr(method, "_datadog_patch"):
                    try:
                        del method._datadog_patch
                    except (AttributeError, TypeError):
                        pass
        _unwrap_llm_subclasses(subcls)


def unpatch():
    import llama_index.core as llama_index_core

    global _llama_index_mod

    if not getattr(llama_index_core, "_datadog_patch", False):
        return

    llama_index_core._datadog_patch = False
    _llama_index_mod = None

    from llama_index.core.base.base_query_engine import BaseQueryEngine
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from llama_index.core.base.llms.base import BaseLLM

    unwrap(BaseQueryEngine, "query")
    unwrap(BaseQueryEngine, "aquery")

    unwrap(BaseRetriever, "retrieve")
    unwrap(BaseRetriever, "aretrieve")

    unwrap(BaseLLM, "__init__")

    # Unwrap LLM methods from all known subclasses (deep traversal)
    _unwrap_llm_subclasses(BaseLLM)

    unwrap(BaseEmbedding, "get_query_embedding")
    unwrap(BaseEmbedding, "aget_query_embedding")
    unwrap(BaseEmbedding, "get_text_embedding_batch")
    unwrap(BaseEmbedding, "aget_text_embedding_batch")

    try:
        from llama_index.core.agent.workflow.multi_agent_workflow import AgentWorkflow
        from llama_index.core.agent.workflow.react_agent import ReActAgent

        unwrap(ReActAgent, "take_step")
        unwrap(AgentWorkflow, "run_agent_step")
    except ImportError:
        pass

    delattr(llama_index_core, "_datadog_integration")
