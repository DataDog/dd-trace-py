from typing import Dict

import vertexai

# Force the generative_models module to load
from vertexai.generative_models import GenerativeModel  # noqa:F401

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.events.llm import LlmRequestEvent
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.vertexai._utils import VertexAIAsyncStreamHandler
from ddtrace.contrib.internal.vertexai._utils import VertexAIStreamHandler
from ddtrace.internal import core
from ddtrace.llmobs._integrations import VertexAIIntegration
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name


config._add("vertexai", {})


def get_version():
    # type: () -> str
    return getattr(vertexai, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"vertexai": ">=1.71.1"}


def _create_llm_event(pin, integration, instance, func, kwargs, provider_name, model_name):
    """Create an LlmRequestEvent for a VertexAI call."""
    return LlmRequestEvent(
        service=int_service(pin, integration.integration_config),
        resource="%s.%s" % (instance.__class__.__name__, func.__name__),
        integration_name="vertexai",
        provider=provider_name,
        model=model_name,
        integration=integration,
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        base_tag_kwargs={
            "provider": provider_name,
            "model": model_name,
        },
        measured=True,
    )


@with_traced_module
def traced_generate(vertexai, pin, func, instance, args, kwargs):
    return _traced_generate(vertexai, pin, func, instance, args, kwargs, instance, False)


@with_traced_module
async def traced_agenerate(vertexai, pin, func, instance, args, kwargs):
    return await _traced_agenerate(vertexai, pin, func, instance, args, kwargs, instance, False)


@with_traced_module
def traced_send_message(vertexai, pin, func, instance, args, kwargs):
    return _traced_generate(vertexai, pin, func, instance, args, kwargs, instance._model, True)


@with_traced_module
async def traced_send_message_async(vertexai, pin, func, instance, args, kwargs):
    return await _traced_agenerate(vertexai, pin, func, instance, args, kwargs, instance._model, True)


def _traced_generate(vertexai, pin, func, instance, args, kwargs, model_instance, is_chat):
    integration = vertexai._datadog_integration
    stream = kwargs.get("stream", False)
    provider_name, model_name = extract_provider_and_model_name(instance=model_instance, model_name_attr="_model_name")
    event = _create_llm_event(pin, integration, instance, func, kwargs, provider_name, model_name)
    # history must be copied since it is modified during the LLM interaction
    history = getattr(instance, "history", [])[:]

    with core.context_with_event(event) as ctx:
        generations = None
        try:
            generations = func(*args, **kwargs)
            if stream:
                ctx.set_item("is_stream", True)
                event._end_span = False
                return make_traced_stream(
                    generations,
                    VertexAIStreamHandler(
                        integration,
                        ctx.span,
                        args,
                        kwargs,
                        is_chat=is_chat,
                        history=history,
                        model_instance=model_instance,
                    ),
                )
        finally:
            if not ctx.get_item("is_stream", False):
                kwargs["instance"] = model_instance
                kwargs["history"] = history
                ctx.set_item("response", generations)
                ctx.set_item("llmobs_args", args)
    return generations


async def _traced_agenerate(vertexai, pin, func, instance, args, kwargs, model_instance, is_chat):
    integration = vertexai._datadog_integration
    stream = kwargs.get("stream", False)
    provider_name, model_name = extract_provider_and_model_name(instance=model_instance, model_name_attr="_model_name")
    event = _create_llm_event(pin, integration, instance, func, kwargs, provider_name, model_name)
    # history must be copied since it is modified during the LLM interaction
    history = getattr(instance, "history", [])[:]

    with core.context_with_event(event) as ctx:
        generations = None
        try:
            generations = await func(*args, **kwargs)
            if stream:
                ctx.set_item("is_stream", True)
                event._end_span = False
                return make_traced_stream(
                    generations,
                    VertexAIAsyncStreamHandler(
                        integration,
                        ctx.span,
                        args,
                        kwargs,
                        is_chat=is_chat,
                        history=history,
                        model_instance=model_instance,
                    ),
                )
        finally:
            if not ctx.get_item("is_stream", False):
                kwargs["instance"] = model_instance
                kwargs["history"] = history
                ctx.set_item("response", generations)
                ctx.set_item("llmobs_args", args)
    return generations


def patch():
    if getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = True

    Pin().onto(vertexai)
    integration = VertexAIIntegration(integration_config=config.vertexai)
    vertexai._datadog_integration = integration

    wrap("vertexai", "generative_models.GenerativeModel.generate_content", traced_generate(vertexai))
    wrap("vertexai", "generative_models.GenerativeModel.generate_content_async", traced_agenerate(vertexai))
    wrap("vertexai", "generative_models.ChatSession.send_message", traced_send_message(vertexai))
    wrap("vertexai", "generative_models.ChatSession.send_message_async", traced_send_message_async(vertexai))


def unpatch():
    if not getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = False

    unwrap(vertexai.generative_models.GenerativeModel, "generate_content")
    unwrap(vertexai.generative_models.GenerativeModel, "generate_content_async")
    unwrap(vertexai.generative_models.ChatSession, "send_message")
    unwrap(vertexai.generative_models.ChatSession, "send_message_async")

    delattr(vertexai, "_datadog_integration")
