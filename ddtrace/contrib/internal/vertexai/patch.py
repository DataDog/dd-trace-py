import sys

import vertexai

# Force the generative_models module to load
from vertexai.generative_models import GenerativeModel  # noqa:F401

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name
from ddtrace.contrib.internal.stream_handler import make_traced_stream
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.vertexai._constants import COMPONENT
from ddtrace.contrib.internal.vertexai._constants import TAG_MODEL
from ddtrace.contrib.internal.vertexai._constants import TAG_PROVIDER
from ddtrace.contrib.internal.vertexai._utils import VertexAIAsyncStreamHandler
from ddtrace.contrib.internal.vertexai._utils import VertexAIStreamHandler
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.span_bus import span_from_context


config._add("vertexai", {})


def get_version() -> str:
    return getattr(vertexai, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"vertexai": ">=1.71.1"}


def traced_generate(func, instance, args, kwargs):
    return _traced_generate(func, instance, args, kwargs, instance, False)


async def traced_agenerate(func, instance, args, kwargs):
    return await _traced_agenerate(func, instance, args, kwargs, instance, False)


def traced_send_message(func, instance, args, kwargs):
    return _traced_generate(func, instance, args, kwargs, instance._model, True)


async def traced_send_message_async(func, instance, args, kwargs):
    return await _traced_agenerate(func, instance, args, kwargs, instance._model, True)


def _request_event(func, instance, args, kwargs, model_instance):
    provider_name, model_name = extract_provider_and_model_name(instance=model_instance, model_name_attr="_model_name")
    # LLMObs reads the model instance and the chat history from the request kwargs. History must be
    # copied before the call since it is modified during the LLM interaction.
    request_kwargs = dict(kwargs, instance=model_instance, history=getattr(instance, "history", [])[:])
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.vertexai,
        service=int_service(None, config.vertexai),
        resource="%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        tags={TAG_PROVIDER: provider_name, TAG_MODEL: model_name},
        submit_to_llmobs=True,
        request_args=args,
        request_kwargs=request_kwargs,
        instance=model_instance,
    )


def _traced_generate(func, instance, args, kwargs, model_instance, is_chat):
    event = _request_event(func, instance, args, kwargs, model_instance)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            event.response = func(*args, **kwargs)
            return event.response
    # The stream handler dispatches the ended event once the stream is consumed or closed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            generations = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        handler = VertexAIStreamHandler(
            None, span_from_context(ctx), args, event.request_kwargs, ctx=ctx, is_chat=is_chat
        )
        return make_traced_stream(generations, handler)


async def _traced_agenerate(func, instance, args, kwargs, model_instance, is_chat):
    event = _request_event(func, instance, args, kwargs, model_instance)
    if not kwargs.get("stream", False):
        with core.context_with_event(event):
            event.response = await func(*args, **kwargs)
            return event.response
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            generations = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        handler = VertexAIAsyncStreamHandler(
            None, span_from_context(ctx), args, event.request_kwargs, ctx=ctx, is_chat=is_chat
        )
        return make_traced_stream(generations, handler)


def patch():
    if getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = True

    wrap("vertexai", "generative_models.GenerativeModel.generate_content", traced_generate)
    wrap("vertexai", "generative_models.GenerativeModel.generate_content_async", traced_agenerate)
    wrap("vertexai", "generative_models.ChatSession.send_message", traced_send_message)
    wrap("vertexai", "generative_models.ChatSession.send_message_async", traced_send_message_async)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)


def unpatch():
    if not getattr(vertexai, "_datadog_patch", False):
        return

    vertexai._datadog_patch = False

    integration_hooks.on_unpatch(COMPONENT)

    unwrap(vertexai.generative_models.GenerativeModel, "generate_content")
    unwrap(vertexai.generative_models.GenerativeModel, "generate_content_async")
    unwrap(vertexai.generative_models.ChatSession, "send_message")
    unwrap(vertexai.generative_models.ChatSession, "send_message_async")
