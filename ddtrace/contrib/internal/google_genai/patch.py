import sys

from google import genai

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.google_genai._constants import COMPONENT
from ddtrace.contrib.internal.google_genai._constants import TAG_MODEL
from ddtrace.contrib.internal.google_genai._constants import TAG_PROVIDER
from ddtrace.contrib.internal.google_genai._utils import GoogleGenAIAsyncStreamHandler
from ddtrace.contrib.internal.google_genai._utils import GoogleGenAIStreamHandler
from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.streaming import make_traced_stream


config._add("google_genai", {})


def _supported_versions():
    return {"google.genai": ">=1.21.1"}


def get_version() -> str:
    return getattr(genai, "__version__", "")


def _request_event(func, instance, kwargs, operation):
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.google_genai,
        service=int_service(None, config.google_genai),
        resource="%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        tags={TAG_PROVIDER: provider_name, TAG_MODEL: model_name},
        submit_to_llmobs=True,
        request_kwargs=kwargs,
        instance=instance,
        operation=operation,
    )


def traced_generate(func, instance, args, kwargs):
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event):
        event.response = func(*args, **kwargs)
        return event.response


async def traced_async_generate(func, instance, args, kwargs):
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event):
        event.response = await func(*args, **kwargs)
        return event.response


def traced_generate_stream(func, instance, args, kwargs):
    event = _request_event(func, instance, kwargs, "llm")
    # The stream handler dispatches the ended event once the stream is consumed or closed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        handler = GoogleGenAIStreamHandler(None, span_from_context(ctx), args, kwargs, ctx=ctx)
        return make_traced_stream(resp, handler)


async def traced_async_generate_stream(func, instance, args, kwargs):
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        handler = GoogleGenAIAsyncStreamHandler(None, span_from_context(ctx), args, kwargs, ctx=ctx)
        return make_traced_stream(resp, handler)


def traced_embed_content(func, instance, args, kwargs):
    event = _request_event(func, instance, kwargs, "embedding")
    with core.context_with_event(event):
        event.response = func(*args, **kwargs)
        return event.response


async def traced_async_embed_content(func, instance, args, kwargs):
    event = _request_event(func, instance, kwargs, "embedding")
    with core.context_with_event(event):
        event.response = await func(*args, **kwargs)
        return event.response


def patch():
    if getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = True

    wrap("google.genai", "models.Models.generate_content", traced_generate)
    wrap("google.genai", "models.Models.generate_content_stream", traced_generate_stream)
    wrap("google.genai", "models.AsyncModels.generate_content", traced_async_generate)
    wrap("google.genai", "models.AsyncModels.generate_content_stream", traced_async_generate_stream)
    wrap("google.genai", "models.Models.embed_content", traced_embed_content)
    wrap("google.genai", "models.AsyncModels.embed_content", traced_async_embed_content)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch("google_genai")


def unpatch():
    if not getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = False

    integration_hooks.on_unpatch("google_genai")

    unwrap(genai.models.Models, "generate_content")
    unwrap(genai.models.Models, "generate_content_stream")
    unwrap(genai.models.AsyncModels, "generate_content")
    unwrap(genai.models.AsyncModels, "generate_content_stream")
    unwrap(genai.models.Models, "embed_content")
    unwrap(genai.models.AsyncModels, "embed_content")
