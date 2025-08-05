import sys

from google import genai

from ddtrace import config
from ddtrace.contrib.internal.google_genai._utils import TracedAsyncGoogleGenAIStreamResponse
from ddtrace.contrib.internal.google_genai._utils import TracedGoogleGenAIStreamResponse
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import GoogleGenAIIntegration
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name
from ddtrace.trace import Pin


config._add("google_genai", {})


def _supported_versions():
    return {"google.genai": ">=1.21.1"}


def get_version() -> str:
    return getattr(genai, "__version__", "")


@with_traced_module
def traced_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="llm")


@with_traced_module
async def traced_async_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="llm")


@with_traced_module
def traced_generate_stream(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    )
    try:
        resp = func(*args, **kwargs)
        return TracedGoogleGenAIStreamResponse(resp, integration, span, args, kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="llm")
        span.finish()
        raise


@with_traced_module
async def traced_async_generate_stream(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    )
    try:
        resp = await func(*args, **kwargs)
        return TracedAsyncGoogleGenAIStreamResponse(resp, integration, span, args, kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="llm")
        span.finish()
        raise


@with_traced_module
def traced_embed_content(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="embedding")


@with_traced_module
async def traced_async_embed_content(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="embedding")


def patch():
    if getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = True
    Pin().onto(genai)
    integration = GoogleGenAIIntegration(integration_config=config.google_genai)
    genai._datadog_integration = integration

    wrap("google.genai", "models.Models.generate_content", traced_generate(genai))
    wrap("google.genai", "models.Models.generate_content_stream", traced_generate_stream(genai))
    wrap("google.genai", "models.AsyncModels.generate_content", traced_async_generate(genai))
    wrap("google.genai", "models.AsyncModels.generate_content_stream", traced_async_generate_stream(genai))
    wrap("google.genai", "models.Models.embed_content", traced_embed_content(genai))
    wrap("google.genai", "models.AsyncModels.embed_content", traced_async_embed_content(genai))


def unpatch():
    if not getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = False

    unwrap(genai.models.Models, "generate_content")
    unwrap(genai.models.Models, "generate_content_stream")
    unwrap(genai.models.AsyncModels, "generate_content")
    unwrap(genai.models.AsyncModels, "generate_content_stream")
    unwrap(genai.models.Models, "embed_content")
    unwrap(genai.models.AsyncModels, "embed_content")

    delattr(genai, "_datadog_integration")
