import sys
from typing import Dict

from ddtrace import config
from ddtrace.contrib.events.llm import LlmRequestEvent
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.llmobs._integrations import LlamaIndexIntegration


log = get_logger(__name__)


config._add("llama_index", {"_default_service": schematize_service_name("llama_index")})


def get_version() -> str:
    from llama_index.core import __version__

    return __version__


def _supported_versions() -> Dict[str, str]:
    return {"llama_index": ">=0.10.0"}


def _get_llama_index():
    return sys.modules["llama_index"]


def _create_llm_event(integration, operation, kwargs):
    """Create an LlmRequestEvent for a llama_index operation."""
    event = LlmRequestEvent(
        service=config.llama_index._default_service,
        resource=operation,
        integration_name="llama_index",
        provider="llama_index",
        model="",
        integration=integration,
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        base_tag_kwargs={
            "provider": "llama_index",
        },
        measured=True,
    )
    event.set_span_name("llama_index.%s" % operation)
    return event


def _traced_query(func, instance, args, kwargs):
    """Trace a query engine query call."""
    integration = _get_llama_index()._datadog_integration
    event = _create_llm_event(integration, "query", kwargs)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            ctx.set_item("response", result)
            ctx.set_item("_dd_query_input", str(args[0]) if args else "")
    return result


async def _traced_aquery(func, instance, args, kwargs):
    """Trace an async query engine query call."""
    integration = _get_llama_index()._datadog_integration
    event = _create_llm_event(integration, "aquery", kwargs)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = await func(*args, **kwargs)
        finally:
            ctx.set_item("response", result)
            ctx.set_item("_dd_query_input", str(args[0]) if args else "")
    return result


def _traced_retrieve(func, instance, args, kwargs):
    """Trace a retriever retrieve call."""
    integration = _get_llama_index()._datadog_integration
    event = _create_llm_event(integration, "retrieve", kwargs)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            ctx.set_item("response", result)
            ctx.set_item("_dd_query_input", str(args[0]) if args else "")
    return result


def _traced_synthesize(func, instance, args, kwargs):
    """Trace a synthesizer synthesize call."""
    integration = _get_llama_index()._datadog_integration
    event = _create_llm_event(integration, "synthesize", kwargs)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            ctx.set_item("response", result)
    return result


def _traced_run(func, instance, args, kwargs):
    """Trace an ingestion pipeline run call."""
    integration = _get_llama_index()._datadog_integration
    event = _create_llm_event(integration, "run", kwargs)

    with core.context_with_event(event) as ctx:
        result = None
        try:
            result = func(*args, **kwargs)
        finally:
            ctx.set_item("response", result)
    return result


def patch():
    """Activate tracing for llama_index."""
    import llama_index

    if getattr(llama_index, "_datadog_patch", False):
        return

    llama_index._datadog_patch = True

    integration = LlamaIndexIntegration(integration_config=config.llama_index)
    llama_index._datadog_integration = integration

    wrap("llama_index.core.base.base_query_engine", "BaseQueryEngine.query", _traced_query)
    wrap("llama_index.core.base.base_query_engine", "BaseQueryEngine.aquery", _traced_aquery)
    wrap("llama_index.core.base.base_retriever", "BaseRetriever.retrieve", _traced_retrieve)
    wrap(
        "llama_index.core.response_synthesizers.base",
        "BaseSynthesizer.synthesize",
        _traced_synthesize,
    )
    wrap("llama_index.core.ingestion.pipeline", "IngestionPipeline.run", _traced_run)


def unpatch():
    """Disable tracing for llama_index."""
    import llama_index
    from llama_index.core.base.base_query_engine import BaseQueryEngine
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.ingestion.pipeline import IngestionPipeline
    from llama_index.core.response_synthesizers.base import BaseSynthesizer

    if not getattr(llama_index, "_datadog_patch", False):
        return

    llama_index._datadog_patch = False

    unwrap(BaseQueryEngine, "query")
    unwrap(BaseQueryEngine, "aquery")
    unwrap(BaseRetriever, "retrieve")
    unwrap(BaseSynthesizer, "synthesize")
    unwrap(IngestionPipeline, "run")

    delattr(llama_index, "_datadog_integration")
