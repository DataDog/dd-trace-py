import sys
from typing import Dict

import llama_index

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import LlamaIndexIntegration


log = get_logger(__name__)


config._add("llama_index", {})


def _supported_versions() -> Dict[str, str]:
    return {"llama_index": ">=0.1.0"}


def get_version() -> str:
    return getattr(llama_index, "__version__", "")


@with_traced_module
def traced_llm_call(llama_index, pin, func, instance, args, kwargs):
    """Trace an LLM call with LLMObs support."""
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
            integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=result)
            span.finish()
    return result


@with_traced_module
async def traced_async_llm_call(llama_index, pin, func, instance, args, kwargs):
    """Trace an async LLM call with LLMObs support."""
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
            integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=result)
            span.finish()
    return result


def patch():
    """Activate tracing for llama_index."""
    if getattr(llama_index, "_datadog_patch", False):
        return

    llama_index._datadog_patch = True

    Pin().onto(llama_index)
    integration = LlamaIndexIntegration(integration_config=config.llama_index)
    llama_index._datadog_integration = integration

    # TODO: Add wrap calls for target methods
    # wrap("llama_index", "Client.chat", traced_llm_call(llama_index))


def unpatch():
    """Disable tracing for llama_index."""
    if not getattr(llama_index, "_datadog_patch", False):
        return

    llama_index._datadog_patch = False

    # TODO: Add unwrap calls
    # unwrap(llama_index.Client, "chat")

    delattr(llama_index, "_datadog_integration")
