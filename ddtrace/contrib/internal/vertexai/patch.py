import sys
from typing import Dict

import vertexai

# Force the generative_models module to load
from vertexai.generative_models import GenerativeModel  # noqa:F401

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.vertexai._utils import VertexAIAsyncStreamHandler
from ddtrace.contrib.internal.vertexai._utils import VertexAIStreamHandler
from ddtrace.llmobs._integrations import VertexAIIntegration
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name


config._add("vertexai", {})


def get_version():
    # type: () -> str
    return getattr(vertexai, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"vertexai": ">=1.71.1"}


def traced_generate(func, instance, args, kwargs):
    return _traced_generate(func, instance, args, kwargs, instance, False)


async def traced_agenerate(func, instance, args, kwargs):
    return await _traced_agenerate(func, instance, args, kwargs, instance, False)


def traced_send_message(func, instance, args, kwargs):
    return _traced_generate(func, instance, args, kwargs, instance._model, True)


async def traced_send_message_async(func, instance, args, kwargs):
    return await _traced_agenerate(func, instance, args, kwargs, instance._model, True)


def _traced_generate(func, instance, args, kwargs, model_instance, is_chat):
    integration = vertexai._datadog_integration
    stream = kwargs.get("stream", False)
    generations = None
    provider_name, model_name = extract_provider_and_model_name(instance=model_instance, model_name_attr="_model_name")
    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    )
    # history must be copied since it is modified during the LLM interaction
    history = getattr(instance, "history", [])[:]
    try:
        generations = func(*args, **kwargs)
        if stream:
            return make_traced_stream(
                generations,
                VertexAIStreamHandler(
                    integration, span, args, kwargs, is_chat=is_chat, history=history, model_instance=model_instance
                ),
            )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if span.error or not stream:
            kwargs["instance"] = model_instance
            kwargs["history"] = history
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=generations)
            span.finish()
    return generations


async def _traced_agenerate(func, instance, args, kwargs, model_instance, is_chat):
    integration = vertexai._datadog_integration
    stream = kwargs.get("stream", False)
    generations = None
    provider_name, model_name = extract_provider_and_model_name(instance=model_instance, model_name_attr="_model_name")
    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    )
    # history must be copied since it is modified during the LLM interaction
    history = getattr(instance, "history", [])[:]
    try:
        generations = await func(*args, **kwargs)
        if stream:
            return make_traced_stream(
                generations,
                VertexAIAsyncStreamHandler(
                    integration, span, args, kwargs, is_chat=is_chat, history=history, model_instance=model_instance
                ),
            )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # streamed spans will be finished separately once the stream generator is exhausted
        if span.error or not stream:
            kwargs["instance"] = model_instance
            kwargs["history"] = history
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=generations)
            span.finish()
    return generations


def patch():
    if getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = True

    integration = VertexAIIntegration(integration_config=config.vertexai)
    vertexai._datadog_integration = integration

    wrap("vertexai", "generative_models.GenerativeModel.generate_content", traced_generate)
    wrap("vertexai", "generative_models.GenerativeModel.generate_content_async", traced_agenerate)
    wrap("vertexai", "generative_models.ChatSession.send_message", traced_send_message)
    wrap("vertexai", "generative_models.ChatSession.send_message_async", traced_send_message_async)


def unpatch():
    if not getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = False

    unwrap(vertexai.generative_models.GenerativeModel, "generate_content")
    unwrap(vertexai.generative_models.GenerativeModel, "generate_content_async")
    unwrap(vertexai.generative_models.ChatSession, "send_message")
    unwrap(vertexai.generative_models.ChatSession, "send_message_async")

    delattr(vertexai, "_datadog_integration")
